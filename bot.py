import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiohttp import web 

# ---- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ----
# Render автоматически устанавливает PORT.
PORT = int(os.environ.get("PORT", 8080))
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
    logging.info(f"Поймано сообщение от канала: {message.sender_chat.title} в чате ID: {message.chat.id}")
    
    try:
        # 1. Удаляем сообщение, написанное от имени канала
        await message.delete()
        
        # 2. Отправляем предупреждение:
        # message_thread_id=message.message_thread_id гарантирует, что ответ 
        # попадет в ту же ветку комментариев (топик).
        await message.answer(
            WARNING_TEXT, 
            message_thread_id=message.message_thread_id
        )
        
        logging.info(f"Сообщение от {message.sender_chat.title} удалено, отправлено предупреждение в ветку.")
    
    except Exception as e:
        # Обработка ошибок (например, если у бота нет прав)
        logging.error(f"Ошибка при обработке сообщения от канала: {e}")


# ----------------------------------------
# БЛОК ДЛЯ WEB SERVICE НА RENDER (для предотвращения сна)
# ----------------------------------------

# Фиктивная функция для ответа на HTTP-запрос (для UptimeRobot)
async def health_check(request):
    return web.Response(text="Bot is running (via Polling)")

# Функция, которая запускает бота на Polling и фиктивный веб-сервер
async def start_bot_and_server():
    # Запускаем бота на Polling как фоновую задачу
    polling_task = asyncio.create_task(bot.delete_webhook(drop_pending_updates=True))
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    # Создаем и запускаем фиктивный веб-сервер
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    
    runner = web.AppRunner(app)
    await runner.setup()
    # Сервер слушает на '0.0.0.0' и порту, который требует Render
    site = web.TCPSite(runner, '0.0.0.0', PORT) 
    await site.start()
    
    logging.info(f"Web server started on port {PORT}. Polling running in background.")
    
    # Ждем завершения задачи Polling
    await polling_task


if __name__ == "__main__":
    if TOKEN is None:
        # Этого не должно произойти, если переменные окружения установлены правильно
        logging.critical("Критическая ошибка: не найден TELEGRAM_TOKEN в переменных окружения!")
    else:
        try:
            logging.info("Бот запускается в режиме Web Service...")
            asyncio.run(start_bot_and_server())
        except KeyboardInterrupt:
            logging.info("Bot stopped by KeyboardInterrupt.")
