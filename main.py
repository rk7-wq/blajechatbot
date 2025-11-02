# main.py — BlajeChatBot (модерация комментариев к каналу через webhook)
# Требуется: python-telegram-bot==21.8, Flask

import os
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
token = os.environ["token"]                     # <-- ключ в нижнем регистре
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# ID канала, от имени которого писать запрещаем.
# По твоему логу это: blajeru -> -1001786114762
ALLOWED_SENDER_CHAT_IDS = {-1001786114762}

# Текст предупреждения
WARNING_TEXT = (
    "Сообщения **от имени канала** в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите **от своего личного профиля**.\n"
    "Бот Модератор."
)

# Кулдаун на одно предупреждение в одном топике, сек
WARN_COOLDOWN_SECONDS = 2

# ---------- Логи ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("BlajeChatBot")

# ---------- Flask ----------
flask_app = Flask(__name__)

# Очередь кулдауна по (chat_id, thread_id)
_last_warn_time_by_topic: dict[tuple[int, Optional[int]], float] = {}

# ---------- Telegram Application ----------
application: Application = ApplicationBuilder().token(token).build()

async def mod_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляем сообщения, отправленные 'от имени канала' в группе-обсуждении канала
       и отвечаем предупреждением в том же топике."""
    msg = update.effective_message
    if not msg:
        return

    chat = msg.chat
    sender_chat = msg.sender_chat  # если сообщение от имени канала — это заполнено
    thread_id = msg.message_thread_id

    # Диагностика
    log.info(
        "Update: chat_id=%s type=%s sender_chat_id=%s thread_id=%s text=%r",
        chat.id, chat.type, getattr(sender_chat, "id", None), thread_id, msg.text or msg.caption
    )

    # интересуют только группы/супергруппы
    if chat.type not in ("group", "supergroup"):
        return

    # если не от имени канала — ничего не делаем
    if sender_chat is None:
        return

    # Если это канал (id < 0 и ~-100...), и он НЕ в разрешенных — удаляем
    if sender_chat.id not in ALLOWED_SENDER_CHAT_IDS:
        try:
            await msg.delete()
            log.info("Удалено сообщение от канала sender_chat_id=%s", sender_chat.id)
        except Forbidden as e:
            log.warning("Нет прав на удаление: %s", e)
            return
        except BadRequest as e:
            log.warning("Не удалось удалить сообщение: %s", e)
            return

        # антииспам по топикам
        import time
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
                    message_thread_id=thread_id,  # важно для комментариев (топиков)
                )
                log.info("Отправлено предупреждение в топик %s", thread_id)
            except Forbidden as e:
                log.warning("Нет прав на отправку сообщения: %s", e)
            except BadRequest as e:
                log.warning("Ошибка при отправке предупреждения: %s", e)

# фильтр: любые сообщения/медиа/репосты в группах
application.add_handler(MessageHandler(
    filters.ChatType.GROUPS & (filters.ALL),
    mod_handler
))

# ---------- Webhook endpoints ----------
@flask_app.get("/health")
def health():
    return jsonify(ok=True)

@flask_app.post(f"/telegram/{token}")
def telegram_webhook():
    """Точка входа для Telegram. Flask получает JSON и кладёт в очередь PTB."""
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.update_queue.put_nowait(update)
    except Exception as e:
        log.exception("Ошибка обработки апдейта: %s", e)
    return "ok", 200

# ---------- Запуск ----------
async def setup_webhook():
    """Чистим вебхук и ставим новый на наш URL."""
    if not PUBLIC_URL:
        log.error("PUBLIC_URL не задан. Укажи его в Render → Environment.")
        return
    webhook_url = f"{PUBLIC_URL}/telegram/{token}"
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "channel_post"],
            max_connections=40,
        )
        me = await application.bot.get_me()
        log.info("Webhook установлен: %s, бот: @%s", webhook_url, me.username)
        log.info("Разрешённый канал(ы): %s", ", ".join(map(str, ALLOWED_SENDER_CHAT_IDS)))
    except Exception as e:
        log.exception("Не удалось установить webhook: %s", e)

if __name__ == "__main__":
    import asyncio

    # на Render требуется слушать 0.0.0.0 и порт из окружения
    port = int(os.environ.get("PORT", "1000"))

    # поднимаем PTB (без polling) и ставим webhook
    asyncio.get_event_loop().create_task(application.initialize())
    asyncio.get_event_loop().create_task(setup_webhook())
    asyncio.get_event_loop().create_task(application.start())

    # запускаем Flask (он принимает HTTPS от Render и проксит апдейты в PTB)
    flask_app.run(host="0.0.0.0", port=port)
