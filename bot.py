import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiohttp import web 

# ---- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ----
# Render автоматически устанавливает PORT.
PORT = int(os.environ.get("PORT", 10000)) # Используем 10000 как стандарт для Web Service
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
# !!! ГЛАВНЫЙ ХЕНДЛЕР: Удаление сообщений и ответ в ветку комментариев !!!
# 
@dp.message(F.sender_chat)
async def delete_channel_messages(message: Message):
    # Логгируем, что мы поймали сообщение от имени канала
    logging.info(f"Поймано сообщение от канала: {message.sender_chat.title}")
    
    try:
        # 1. Удаляем сообщение
        await message.delete()
        
        # 2. Отправляем предупреждение:
        # message.answer(WARNING_TEXT) — в aiogram 3 это автоматически 
        # отправляет ответ в ту же ветку/топик, откуда пришло сообщение, 
        # если message_thread_id существует.
        await message.answer(WARNING_TEXT)
        
        logging.info(f"Сообщение от {message.sender_chat.title} удалено, отправлено предупреждение.")
    
    except Exception as e:
        # Обработка ошибок (например, если у бота нет прав)
        logging.error(f"Ошибка при обработке сообщения от канала (после удаления): {e}")


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
