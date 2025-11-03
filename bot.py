import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
# Импортируем для создания фиктивного веб-сервера
from aiohttp import web 

# ---- НАСТРОЙКА ----
# В Render переменная окружения PORT автоматически устанавливается.
PORT = int(os.environ.get("PORT", 8080))
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Текст твоего предупреждения
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)
# ---------------------

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Хендлеры (Остаются без изменений)
@dp.message(CommandStart())
async def send_welcome(message: Message):
    await message.reply("Бот-модератор запущен и готов к работе!")

@dp.message(F.sender_chat)
async def delete_channel_messages(message: Message):
    logging.info(f"Поймано сообщение от канала: {message.sender_chat.title}")
    
    try:
        await message.delete()
        await message.answer(WARNING_TEXT)
        logging.info(f"Сообщение от {message.sender_chat.title} удалено, отправлено предупреждение.")
    except Exception as e:
        logging.error(f"Ошибка: не удалось удалить сообщение или отправить ответ: {e}")

# ----------------------------------------
# !!! НОВЫЙ БЛОК ДЛЯ ОБХОДА Render !!!
# ----------------------------------------

# 1. Функция, которая просто отвечает на запрос, чтобы Render увидел порт.
async def health_check(request):
    return web.Response(text="Bot is running (via Polling)")

# 2. Функция, которая запускает бота и веб-сервер
async def start_bot_and_server():
    # Запускаем бота на Polling как фоновую задачу
    polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
    
    # Запускаем фиктивный веб-сервер
    app = web.Application()
    # Регистрируем фиктивный хендлер, который Render будет "пинговать"
    app.add_routes([web.get('/', health_check)])
    
    # Запускаем веб-сервер на порту, который требует Render
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logging.info(f"Web server started on port {PORT}. Polling running in background.")
    
    # Ждем завершения фоновой задачи (бота), хотя на практике она будет работать всегда.
    await polling_task


if __name__ == "__main__":
    if TOKEN is None:
        logging.critical("Критическая ошибка: не найден TELEGRAM_TOKEN!")
    else:
        try:
            logging.info("Бот запускается в режиме Web Service...")
            # Запускаем асинхронную функцию
            asyncio.run(start_bot_and_server())
        except KeyboardInterrupt:
            logging.info("Bot stopped.")
