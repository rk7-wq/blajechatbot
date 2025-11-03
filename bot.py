import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiohttp import web 

# ---- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ----
# Render автоматически устанавливает PORT.
PORT = int(os.environ.get("PORT", 10000)) 
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Текст предупреждения
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)
# ----------------------------------------

# Настройка логгирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()


# Хендлер на команду /start
@dp.message(CommandStart())
async def send_welcome(message: Message):
    await message.reply("Бот-модератор запущен и готов к работе!")


# 
# !!! ГЛАВНЫЙ ХЕНДЛЕР: Ответ (reply) в ветку, затем удаление исходного сообщения !!!
# 
@dp.message(F.sender_chat)
async def delete_channel_messages(message: Message):
    # Логгируем параметры сообщения для диагностики
    logging.info(
        f"Поймано сообщение от канала: {message.sender_chat.title}. "
        f"Message ID: {message.message_id}. "
        f"Thread ID (комментарий): {message.message_thread_id}"
    )
    
    try:
        # 1. СНАЧАЛА ОТПРАВЛЯЕМ ПРЕДУПРЕЖДЕНИЕ (ответом на исходное сообщение)
        # Это заставляет Telegram API разместить ответ в той же ветке/чате, 
        # при этом сообщение message.message_id еще не удалено.
        await bot.send_message(
            chat_id=message.chat.id,
            text=WARNING_TEXT,
            reply_to_message_id=message.message_id, # Ключевой элемент для ответа в ветку/чат
            # message_thread_id здесь не нужен, т.к. reply_to_message_id надежнее
        )
        
        # 2. ЗАТЕМ УДАЛЯЕМ СООБЩЕНИЕ
        await message.delete()
        
        logging.info(f"Сообщение от {message.sender_chat.title} удалено, предупреждение отправлено.")
    
    except Exception as e:
        # Логирование ошибок
        logging.error(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось отправить ответ/удалить сообщение. Ошибка: {e}.")


# ----------------------------------------
# БЛОК ДЛЯ WEB SERVICE НА RENDER (для предотвращения сна и конфликтов)
# ----------------------------------------

# Фиктивная функция для ответа на HTTP-запрос (для Render и UptimeRobot)
async def health_check(request):
    return web.Response(text="Bot is running (via Polling)")

# Функция, которая запускает бота на Polling и фиктивный веб-сервер
async def start_bot_and_server():
    
    # 1. Сбрасываем все ожидающие обновления и старые подключения Polling.
    await bot.delete_webhook(drop_pending_updates=True) 

    # 2. Запускаем Polling как фоновую задачу
    polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
    
    # 3. Создаем и запускаем фиктивный веб-сервер
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT) 
    await site.start()
    
    logging.info(f"Web server started on port {PORT}. Polling running in background.")
    
    # 4. Ждем завершения задачи Polling
    await polling_task


if __name__ == "__main__":
    if TOKEN is None:
        logging.critical("Критическая ошибка: не найден TELEGRAM_TOKEN в переменных окружения!")
    else:
        try:
            logging.info("Бот запускается в режиме Web Service...")
            asyncio.run(start_bot_and_server())
        except KeyboardInterrupt:
            logging.info("Bot stopped by KeyboardInterrupt.")
