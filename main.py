# main.py — BlajeChatBot (Бот Модератор)
# Требуется: python-telegram-bot==21.8, flask
# Режим работы: polling (без вебхука), Flask используется для "/" и "/health".
# Бот удаляет сообщения «от имени канала» и пишет предупреждение,
# но разрешает писать от заданных каналов (например, @blajeru).

import os
import time
import logging
from threading import Thread

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------- Настройки и окружение -----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("BlajeChatBot")

# TOKEN: обязателен. В Render мы использовали переменную окружения с ключом "token" (строчные!)
TOKEN = os.environ.get("token")
if not TOKEN:
    raise RuntimeError(
        "Переменная окружения 'token' не найдена. "
        "Добавьте её в Render → Environment → Add Environment Variable."
    )

# PUBLIC_URL: не обязательно, просто логируем для наглядности
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8080")
PORT = int(os.environ.get("PORT", "8080"))

# Каналы, которым разрешено писать «от имени канала».
# По умолчанию разрешаем @blajeru (=-1001786114762). Можно расширить через ENV:
# ALLOWED_SENDER_CHATS="-1001786114762,-1001234567890"
ALLOWED_SENDER_CHAT_IDS = {-1001786114762}
_env_allowed = os.environ.get("ALLOWED_SENDER_CHATS")
if _env_allowed:
    try:
        extra_ids = {int(x.strip()) for x in _env_allowed.split(",") if x.strip()}
        ALLOWED_SENDER_CHAT_IDS |= extra_ids
    except Exception:
        logger.warning("Не удалось распарсить ALLOWED_SENDER_CHATS, используем дефолтный набор.")

# Текст предупреждения
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)

# Анти-спам предупреждений: интервал между предупреждениями в одном чате (сек)
WARN_COOLDOWN_SECONDS = 2
_last_warn_time_by_chat: dict[int, float] = {}

# ----------------- Flask (keep-alive + health) -----------------

def make_flask_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def root():
        return "BlajeChatBot работает!"

    @app.get("/health")
    def health():
        return "ok"

    return app

flask_app = make_flask_app()

# ----------------- Telegram handlers -----------------

async def delete_and_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет сообщение, отправленное 'от имени канала' (если канал не в белом списке),
    и пишет предупреждение в тот же чат/топик с анти-спам кулдауном."""
    msg = update.effective_message
    if not msg:
        return

    # Канальный пост в группе/супергруппе определяется по sender_chat
    sc = msg.sender_chat
    if not sc:
        return  # обычный пользователь — пропускаем

    # Разрешённые каналы
    if sc.id in ALLOWED_SENDER_CHAT_IDS:
        logger.info("Сообщение от разрешённого канала (sender_chat_id=%s) — пропускаем.", sc.id)
        return

    # Пытаемся удалить «запрещённое» сообщение от имени канала
    try:
        await msg.delete()
        logger.info("Удалено сообщение от канала (sender_chat_id=%s)", sc.id)
    except Exception as e:
        logger.warning("Не удалось удалить сообщение: %s", e)

    # Анти-спам: предупреждение не чаще, чем раз в N секунд на чат
    chat_id = msg.chat_id
    now = time.time()
    last = _last_warn_time_by_chat.get(chat_id, 0.0)
    if now - last < WARN_COOLDOWN_SECONDS:
        return
    _last_warn_time_by_chat[chat_id] = now

    # Пишем предупреждение в тот же чат. Если это топик — используем message_thread_id.
    send_kwargs = {}
    if getattr(msg, "is_topic_message", False) and getattr(msg, "message_thread_id", None) is not None:
        send_kwargs["message_thread_id"] = msg.message_thread_id

    try:
        await context.bot.send_message(chat_id=chat_id, text=WARNING_TEXT, **send_kwargs)
    except Exception as e:
        logger.warning("Не удалось отправить предупреждение: %s", e)

def build_application() -> Application:
    logger.info("Using PUBLIC_URL = %s", PUBLIC_URL)
    app = Application.builder().token(TOKEN).build()

    # Один универсальный обработчик на все типы сообщений — нам важно лишь sender_chat
    app.add_handler(MessageHandler(filters.ALL, delete_and_warn))

    return app

async def run_tg_polling() -> None:
    """Запуск Telegram-бота в режиме polling (без вебхука)."""
    application = build_application()

    # На всякий случай убираем вебхук (если был) и сбрасываем «хвосты»
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    logger.info("BlajeChatBot: запускаем polling…")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

# ----------------- Точка входа -----------------

def _start_tg_in_thread():
    import asyncio
    asyncio.run(run_tg_polling())

if __name__ == "__main__":
    # Telegram в отдельном потоке, Flask — как web-сервис Render ("/" и "/health")
    Thread(target=_start_tg_in_thread, daemon=True).start()

    logger.info("Flask: стартуем на 0.0.0.0:%s", PORT)
    flask_app.run(host="0.0.0.0", port=PORT)
