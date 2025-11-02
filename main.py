# main.py — BlajeChatBot (модерация комментов канала) для Render (webhook + Flask)

import os
import time
import threading
import asyncio
import logging
from typing import Optional

from flask import Flask, request, jsonify
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.ext import (
    Application, ApplicationBuilder, MessageHandler, ContextTypes, filters
)

# ---------- Конфиг ----------
TOKEN = os.environ["token"]                        # ключ в нижнем регистре (Render → Environment)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# Канал, которому разрешаем писать от имени канала (остальных — удаляем)
ALLOWED_SENDER_CHAT_IDS = {-1001786114762}        # blajeru

WARNING_TEXT = (
    "Сообщения **от имени канала** в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите **от своего личного профиля**.\n"
    "Бот Модератор."
)
WARN_COOLDOWN_SECONDS = 2

# ---------- Логи ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("BlajeChatBot")

# ---------- Flask ----------
flask_app = Flask(__name__)

@flask_app.get("/")
def root():
    return "BlajeChatBot is up. Use /health for status.", 200

@flask_app.get("/health")
def health():
    return jsonify(ok=True)

# ---------- Telegram Application ----------
application: Application = ApplicationBuilder().token(TOKEN).build()

_last_warn_time_by_topic: dict[tuple[int, Optional[int]], float] = {}

async def mod_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    chat = msg.chat
    sender_chat = msg.sender_chat
    thread_id = msg.message_thread_id

    log.info(
        "Update: chat_id=%s type=%s sender_chat_id=%s thread_id=%s",
        chat.id, chat.type, getattr(sender_chat, "id", None), thread_id
    )

    if chat.type not in ("group", "supergroup"):
        return
    if sender_chat is None:
        return

    if sender_chat.id not in ALLOWED_SENDER_CHAT_IDS:
        try:
            await msg.delete()
            log.info("Удалено сообщение от канала sender_chat_id=%s", sender_chat.id)
        except (Forbidden, BadRequest) as e:
            log.warning("Не удалось удалить сообщение: %s", e)
            return

        key = (chat.id, thread_id)
        now = time.time()
        last = _last_warn_time_by_topic.get(key, 0.0)
        if now - last >= WARN_COOLDOWN_SECONDS:
            _last_warn_time_by_topic[key] = now
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=WARNING_TEXT,
                    parse_mode=ParseMode.MARKDOWN,
                    message_thread_id=thread_id,
                )
            except (Forbidden, BadRequest) as e:
                log.warning("Не удалось отправить предупреждение: %s", e)

application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.ALL, mod_handler))

@flask_app.post(f"/telegram/{os.environ.get('token','')}")
def telegram_webhook():
    """Приём апдейтов от Telegram → кладём в очередь PTB."""
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.update_queue.put_nowait(update)
    except Exception as e:
        log.exception("Ошибка обработки апдейта: %s", e)
    return "ok", 200

# ---------- Запуск PTB в отдельном asyncio-потоке ----------
async def bot_main():
    if not PUBLIC_URL:
        log.error("PUBLIC_URL не задан. Укажи его в Render → Environment.")
        return
    webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"

    await application.initialize()
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message", "channel_post"],
        max_connections=40,
    )
    me = await application.bot.get_me()
    log.info("Webhook установлен: %s, бот: @%s", webhook_url, me.username)
    log.info("Разрешённый канал(ы): %s", ", ".join(map(str, ALLOWED_SENDER_CHAT_IDS)))

    await application.start()
    # держим цикл живым
    await asyncio.Event().wait()

def start_bot_thread():
    def _runner():
        asyncio.run(bot_main())
    t = threading.Thread(target=_runner, name="ptb-webhook", daemon=True)
    t.start()
    return t

if __name__ == "__main__":
    # запускаем PTB в фоне
    start_bot_thread()

    # запускаем Flask
    port = int(os.environ.get("PORT", "1000"))
    flask_app.run(host="0.0.0.0", port=port)
