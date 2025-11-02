# main.py — BlajeChatBot (удаляет сообщения "от имени канала" и пишет варн)
# Требуется: python-telegram-bot==21.8, Flask

import os
import asyncio
import logging
from flask import Flask, jsonify

from telegram import Update
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, MessageHandler, CommandHandler, ContextTypes, filters
)

# ---------- Логи ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("BlajeChatBot")

# ---------- Конфиг из ENV ----------
TOKEN = os.environ["TOKEN"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
PORT = int(os.environ.get("PORT", "1000"))

WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от личного профиля.\n— Бот Модератор."
)

# ---------- Flask (health) ----------
app = Flask(__name__)

@app.get("/")
def root():
    return "BlajeChatBot работает!"

@app.get("/health")
def health():
    return jsonify(ok=True)

# ---------- Handlers ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text(
            "✅ Бот запущен. Добавьте меня админом в группу обсуждений "
            "и я буду удалять сообщения, отправленные от имени канала."
        )

def _is_channel_identity_message(u: Update) -> bool:
    """
    Возвращает True, если это сообщение в группе/топике,
    отправленное "от имени канала".
    """
    m = u.effective_message
    if not m:
        return False
    # В комментариях к каналу такое сообщение имеет sender_chat (тип channel)
    return m.sender_chat is not None

async def guard_channel_identity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    chat = update.effective_chat
    sc = m.sender_chat

    # Диагностика в логи
    log.info(
        "💬 msg in chat_id=%s type=%s; sender_chat=%s (%s); from_user=%s; topic=%s",
        chat.id, chat.type,
        getattr(sc, "id", None),
        getattr(sc, "type", None) if sc else None,
        getattr(m.from_user, "id", None),
        m.is_topic_message,
    )

    if not _is_channel_identity_message(update):
        return

    # Пытаемся удалить
    try:
        await m.delete()
        log.info("🗑️  Удалил сообщение от имени канала (sender_chat_id=%s) в chat_id=%s", sc.id, chat.id)
    except Forbidden as e:
        log.warning("❌ Нет прав на удаление в chat_id=%s: %s", chat.id, e)
    except BadRequest as e:
        log.warning("⚠️ BadRequest при удалении: %s", e)
    except Exception as e:
        log.exception("💥 Неожиданная ошибка удаления: %s", e)
    else:
        # Пишем варн рядом (в том же чате / топике)
        try:
            if m.is_topic_message and getattr(m, "message_thread_id", None):
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=WARNING_TEXT,
                    message_thread_id=m.message_thread_id
                )
            else:
                await context.bot.send_message(chat_id=chat.id, text=WARNING_TEXT)
        except Forbidden:
            log.warning("Не могу отправить предупреждение (нет права писать) в chat_id=%s", chat.id)
        except Exception as e:
            log.warning("Не удалось отправить предупреждение: %s", e)

async def run():
    # PTB Application
    application = Application.builder().token(TOKEN).concurrent_updates(True).build()

    # Команды
    application.add_handler(CommandHandler("start", cmd_start))

    # Главный фильтр: сообщения в супергруппах/группах/форумных темах, ГДЕ есть sender_chat
    application.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.SenderChat(True),
        guard_channel_identity
    ))

    # Webhook
    webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    await application.bot.set_webhook(url=webhook_url)
    log.info("🌐 Webhook установлен: %s", webhook_url)

    # Запуск веб-сервера PTB (вместе с Flask на том же порту ок)
    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
