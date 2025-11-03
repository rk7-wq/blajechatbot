import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiohttp import web 
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application # Новый импорт

# ---- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ И WEBHOOK ----
# WEB_SERVER_HOST и WEB_SERVER_PORT - адрес и порт, которые слушает Render.
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.environ.get("PORT", 10000)) 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# PUBLIC_URL - полный публичный адрес сервиса Render. 
# Эту переменную (TELEGRAM_WEBHOOK_URL) нужно установить в настройках Render.
PUBLIC_URL = os.getenv("TELEGRAM_WEBHOOK_URL")

# Путь, по которому Telegram будет отправлять обновления.
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = PUBLIC_URL + WEBHOOK_PATH if PUBLIC_URL else None

# ID канала, который НЕ НУЖНО удалять (ваш основной канал)
ALLOWED_SENDER_CHATS = {-1001786114762, }

# Текст предупреждения
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)
# --------------------------------------------------

# Настройка логгирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()


# Хендлер на команду /start
@dp.message(CommandStart())
async def send_welcome(message: Message):
    await message.reply("Бот-модератор запущен в режиме Webhook!")


# 
# !!! ГЛАВНЫЙ ХЕНДЛЕР: Ответ (reply) в ветку, затем удаление исходного сообщения !!!
# 
@dp.message(F.sender_chat)
async def delete_channel_messages(message: Message):
    channel_id = message.sender_chat.id
    
    # --- 1. ПРОВЕРКА НА ИСКЛЮЧЕНИЕ ---
    if channel_id in ALLOWED_SENDER_CHATS:
        logging.info(f"Сообщение от разрешенного канала ID {channel_id} ({message.sender_chat.title}) пропущено.")
        return 
    # ----------------------------------

    logging.info(
        f"Поймано сообщение от канала: {message.sender_chat.title}. "
        f"Channel ID: {channel_id}. "
        f"Message ID: {message.message_id}."
    )
    
    try:
        # 2. СНАЧАЛА ОТПРАВЛЯЕМ ПРЕДУПРЕЖДЕНИЕ (ответом на исходное сообщение)
        await bot.send_message(
            chat_id=message.chat.id,
            text=WARNING_TEXT,
            reply_to_message_id=message.message_id, 
        )
        
        # 3. ЗАТЕМ УДАЛЯЕМ СООБЩЕНИЕ
        await message.delete()
        
        logging.info(f"Сообщение от {message.sender_chat.title} удалено, предупреждение отправлено.")
    
    except Exception as e:
        # Логирование ошибок
        logging.error(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось отправить ответ/удалить сообщение. Ошибка: {e}.")


# ----------------------------------------
# ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА WEBHOOK
# ----------------------------------------

async def on_startup(bot: Bot):
    """Выполняется при запуске бота: устанавливает Webhook и сбрасывает обновления."""
    if WEBHOOK_URL:
        # Устанавливаем Webhook на Telegram API
        # drop_pending_updates=True гарантирует, что старые сообщения не будут обработаны при запуске.
        await bot.set_webhook(
            url=WEBHOOK_URL, 
            drop_pending_updates=True
        )
        logging.info(f"✅ Webhook установлен на: {WEBHOOK_URL}")
    else:
        # Этого не должно случиться, если TELEGRAM_WEBHOOK_URL установлен
        logging.critical("❌ Переменная TELEGRAM_WEBHOOK_URL не установлена. Бот не может работать в режиме Webhook.")
        # Завершаем сессию, чтобы не запустить Polling случайно
        await bot.session.close()
        raise EnvironmentError("WEBHOOK_URL is not configured.")

async def on_shutdown(bot: Bot):
    """Выполняется при остановке бота: удаляет Webhook."""
    logging.info("🧹 Удаление Webhook...")
    await bot.delete_webhook()
    logging.info("❌ Webhook удален. Приложение остановлено.")

def start_bot_webhook():
    """Запускает приложение aiogram/aiohttp в режиме Webhook."""
    
    if TOKEN is None:
        logging.critical("Критическая ошибка: не найден TELEGRAM_TOKEN в переменных окружения!")
        return

    if PUBLIC_URL is None:
        logging.critical("Критическая ошибка: не найдена TELEGRAM_WEBHOOK_URL в переменных окружения! Требуется для режима Webhook.")
        return

    # Регистрируем функции запуска и остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # 1. Создаем AIOHTTP приложение
    app = web.Application()
    
    # 2. Подключаем диспетчер aiogram к AIOHTTP приложению
    # SimpleRequestHandler будет слушать наш WEBHOOK_PATH
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    # 3. Настраиваем приложение aiogram
    setup_application(app, dp, bot=bot)
    
    logging.info(f"🚀 Запуск бота в режиме Webhook на {WEB_SERVER_HOST}:{WEB_SERVER_PORT}...")
    
    # 4. Запуск веб-сервера
    web.run_app(
        app,
        host=WEB_SERVER_HOST,
        port=WEB_SERVER_PORT
    )

if __name__ == "__main__":
    try:
        start_bot_webhook()
    except Exception as e:
        logging.critical(f"Глобальная ошибка при запуске: {e}")
