# main.py — BlajeChatBot (Webhook + Heartbeat, Replit-safe)
# python-telegram-bot==21.8, Flask
# Удаляет сообщения "от имени канала" и пишет предупреждение в ту же ветку (reply_to_message_id).
# Гибрид: webhook + /health + внутренний heartbeat (самопинг каждые 2 мин) для Replit.

import os
import time
import logging
import asyncio
from threading import Thread
from urllib.request import urlopen, Request

from flask import Flask, request, abort

from telegram import Update
from telegram.error import TelegramError, RetryAfter, BadRequest, Forbidden
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    ContextTypes, filters
)

# 👉 ВСТАВЬ СВОИ ДАННЫЕ
TOKEN = "PASTE_YOUR_TOKEN_HERE"
PUBLIC_URL = "https://YOUR-PROJECT.YOUR-LOGIN.replit.dev"  # точный адрес из браузера, без завершающего '/'

# ---------------- Flask (keep-alive + health) ----------------
flask_app = Flask(__name__)

@flask_app.get("/")
def home():
    return "BlajeChatBot работает!"

@flask_app.get("/health")
def health():
    return "OK: BlajeChatBot alive"
# -------------------------------------------------------------

# Логи
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("BlajeChatBot")

# Настройки
WARN_COOLDOWN_SECONDS = 2
ALLOWED_SENDER_CHAT_IDS: set[int] = set()
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)
_last_warn_time: dict[tuple[int, int], float] = {}

# Глобальные для работы из Flask-потока
application: Application | None = None
_main_loop: asyncio.AbstractEventLoop | None = None

# ---------------- PTB Handlers ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ Бот в webhook-режиме. Дайте права 'Delete messages' и 'Send messages'."
    )

async def _warn_in_same_thread(context: ContextTypes.DEFAULT_TYPE, msg) -> bool:
    """Отправляем предупреждение как reply (в ту же ветку)."""
    try:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=WARNING_TEXT,
            reply_to_message_id=msg.message_id,
            allow_sending_without_reply=True,
            disable_notification=True,
            disable_web_page_preview=True,
        )
        return True
    except RetryAfter as e:
        await asyncio.sleep(min(2, int(getattr(e, "retry_after", 1))))
        try:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=WARNING_TEXT,
                reply_to_message_id=msg.message_id,
                allow_sending_without_reply=True,
                disable_notification=True,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as e2:
            logger.error(f"Retry send (reply) failed: {e2}")
    except (BadRequest, Forbidden) as e:
        logger.warning(f"Reply send failed: {e}")

    # Фоллбэк — в общий чат
    try:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=WARNING_TEXT,
            disable_notification=True,
            disable_web_page_preview=True,
        )
        return True
    except TelegramError as e:
        logger.error(f"Fallback send failed: {e}")
        return False

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    sc = msg.sender_chat
    if sc is None:
        return
    if sc.id in ALLOWED_SENDER_CHAT_IDS:
        return

    # Кулдаун по (chat, thread)
    thread_id = getattr(msg, "message_thread_id", None)
    thread_key = (msg.chat_id, thread_id or 0)
    now = time.time()
    last = _last_warn_time.get(thread_key, 0.0)
    if now - last >= WARN_COOLDOWN_SECONDS:
        sent = await _warn_in_same_thread(context, msg)
        if sent:
            _last_warn_time[thread_key] = time.time()

    # Удаляем нарушение
    try:
        await msg.delete()
        logger.info(f"Удалено сообщение от канала (sender_chat_id={sc.id})")
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

# ---------------- Application build/init ----------------
def build_app() -> Application:
    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, handle_all))
    return app

async def init_and_start_app(app: Application):
    """PTB init + установка вебхука + старт."""
    await app.initialize()
    webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"
    await app.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info(f"Webhook set: {webhook_url}")
    await app.start()
    logger.info("Application started (webhook mode)")

# --------------- Heartbeat (самопинг) ----------------
async def heartbeat_task():
    """Раз в 120 сек пингует /health, чтобы контейнер не уходил в idle, пока процесс жив."""
    url = f"{PUBLIC_URL}/health"
    while True:
        try:
            req = Request(url, headers={"User-Agent": "HB/1.0"})
            with urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    logger.info("Heartbeat OK")
                else:
                    logger.warning(f"Heartbeat HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
        await asyncio.sleep(120)  # 2 минуты
# -----------------------------------------------------

# --------------- Flask endpoint (webhook target) ---------------
@flask_app.post(f"/telegram/{TOKEN}")
def telegram_webhook():
    global application, _main_loop
    if application is None or _main_loop is None:
        abort(503)
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        abort(400)
    update = Update.de_json(data, application.bot)
    # обработка апдейта в главной петле
    asyncio.run_coroutine_threadsafe(application.process_update(update), _main_loop)
    return "OK"
# ---------------------------------------------------------------

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

def main():
    global application, _main_loop

    # 1) Flask в отдельном потоке
    Thread(target=run_flask, daemon=True).start()

    # 2) Основная петля и PTB-приложение
    _main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_main_loop)

    application = build_app()
    _main_loop.create_task(init_and_start_app(application))
    _main_loop.create_task(heartbeat_task())  # ← включили самопинг

    logger.info("🚀 BlajeChatBot (Webhook+HB) запущен, ждём входящих апдейтов...")
    try:
        _main_loop.run_forever()
    finally:
        async def shutdown():
            try:
                await application.stop()
                await application.shutdown()
            except Exception:
                pass
        _main_loop.run_until_complete(shutdown())

if __name__ == "__main__":
    # pip install python-telegram-bot==21.8 flask
    main()
