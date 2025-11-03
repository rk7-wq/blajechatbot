import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

# ---- НАСТРОЙКА ----
# Токен бота. Render возьмет его из "Environment Variables"
# НЕ ВПИСЫВАЙ ТОКЕН СЮДА В КОД
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Текст твоего предупреждения
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)
# ---------------------

# Настройка логгирования (чтобы видеть в консоли Render, что бот работает)
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()


# Хендлер на команду /start (просто для проверки, что бот жив)
@dp.message(CommandStart())
async def send_welcome(message: Message):
    await message.reply("Бот-модератор запущен и готов к работе!")


# 
# !!! ГЛАВНЫЙ ХЕНДЛЕР !!!
# 
# Он сработает на ЛЮБОЕ сообщение (текст, фото, стикер)
# у которого есть поле "sender_chat" (отправлено от имени канала).
#
@dp.message(F.sender_chat)
async def delete_channel_messages(message: Message):
    # Логгируем, что мы поймали такое сообщение
    logging.info(f"Поймано сообщение от канала: {message.sender_chat.title}")
    
    try:
        # 1. Удаляем сообщение, написанное от имени канала
        await message.delete()
        
        # 2. Отправляем предупреждение в тот же чат
        # message.answer() отвечает в тот же чат, откуда пришло сообщение
        await message.answer(WARNING_TEXT)
        
        logging.info(f"Сообщение от {message.sender_chat.title} удалено, отправлено предупреждение.")
    
    except Exception as e:
        # Это может случиться, если у бота нет прав в чате
        logging.error(f"Ошибка: не удалось удалить сообщение или отправить ответ: {e}")


# Функция запуска бота
async def main():
    # Пропускаем старые обновления, которые могли накопиться, пока бот был оффлайн
    await bot.delete_webhook(drop_pending_updates=True)
    # Запускаем опрос (polling)
    await dp.start_polling(bot)


if __name__ == "__main__":
    if TOKEN is None:
        logging.critical("Критическая ошибка: не найден TELEGRAM_TOKEN в переменных окружения!")
    else:
        logging.info("Бот запускается...")
        asyncio.run(main())
