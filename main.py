# main.py — BlajeChatBot (бот-модератор)
# Требуется: python-telegram-bot==21.8, flask
# Удаляет сообщения, отправленные "от имени канала" @blajeru,
# а также автоперенаправления из связанного канала в обсуждениях.
# Поддержка keep-alive и health-check через Flask (/ и /healthz).

import os
import logging
import asyncio
from threading import Thread
from flask import Flask, jsonify

from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ------------------ Настройки и логирование ------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
LOG = logging.getLogger("BlajeChatBot")

TOKEN = os.environ["TOKEN"]                  # на Render: переменная окружения TOKEN
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # на Render: https://<your>.onrender.com
PORT = int(os.environ.get("PORT", "1000"))     # Render подставляет порт в PORT

# Канал, от имени которого пишут в группе (должен быть админом в группе!)
CHANNEL_USERNAME = "@blajeru"  # если нужно другое — поменяй

# ------------------ Flask: health/keep-alive ------------------

flask_app = Flask(__name__)

@flask_app.get("/")
def index():
    return "OK", 200

@flask_app.get("/healthz")
def health():
    return jsonify(status="ok"), 200

# ------------------ Логика бота ------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "✅ Бот запущен.\n"
        "Добавьте меня администратором группы (с правом *удалять сообщения*), "
        "и я буду удалять посты, отправленные *от имени канала*."
    )
    await update.effective_message.reply_text(msg, parse_mode="Markdown")

async def resolve_channel_id(app: Application) -> int:
    """
    Получаем numeric ID канала по username и кладём в app.bot_data['channel_id'].
    У публичного канала боту не нужно быть админом, достаточно username.
    """
    chat = await app.bot.get_chat(CHANNEL_USERNAME)
    app.bot_data["channel_id"] = chat.id
    LOG.info("Канал %s -> id=%s", CHANNEL_USERNAME, chat.id)
    return chat.id

async def try_delete(message, context: ContextTypes.DEFAULT_TYPE, reason: str) -> None:
    try:
        await message.delete()
        LOG.info("Удалено (%s): chat_id=%s msg_id=%s", reason, message.chat_id, message.message_id)
    except Forbidden as e:
        LOG.warning("Нет прав на удаление (%s): %s", reason, e)
    except BadRequest as e:
        LOG.warning("Не удалось удалить (%s): %s", reason, e)
    except Exception as e:
        LOG.exception("Ошибка при удалении (%s): %s", reason, e)

async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Единый обработчик для всех сообщений в группах/супергруппах.
    Удаляем, если:
      A) message.sender_chat.id == channel_id  (написали как канал)
      B) message.is_automatic_forward и message.forward_from_chat.id == channel_id
         (автоперенаправление поста из связанного канала)
    """
    m = update.effective_message
    if not m:
        return

    channel_id = context.bot_data.get("channel_id")
    if channel_id is None:
        # на всякий случай догружаем при первом сообщении
        channel_id = await resolve_channel_id(context.application)

    # A) Сообщение "от имени канала"
    if m.sender_chat and m.sender_chat.id == channel_id:
        await try_delete(m, context, "sender_chat==channel")
        return

    # B) Автоперенаправление поста из канала в обсуждение
    if getattr(m, "is_automatic_forward", False) and m.forward_from_chat:
        if m.forward_from_chat.id == channel_id:
            await try_delete(m, context, "auto_forward_from_channel")
            return

    # Для отладки: смотри, что пришло
    # LOG.debug("Пропущено: sender_chat=%s, is_auto=%s, fwd_from=%s",
    #           getattr(m, 'sender_chat', None),
    #           getattr(m, 'is_automatic_forward', None),
    #           getattr(m, 'forward_from_chat', None))

async def bot_runner() -> None:
    """
    Запускаем PTB в режиме webhook на том же порту, который ждёт Render.
    Flask остаётся для / и /healthz (он слушает тот же порт, но другой сервер).
    Поэтому PTB запускаем как самостоятельный aiohttp-сервер.
    """
    application = Application.builder().token(TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", cmd_start))

    # Все сообщения групп/супергрупп
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, on_group_message))

    # Разрешим получать все типы апдейтов (важно для авто-форвардов)
    allowed_updates = list(Update.ALL_TYPES)

    # Получим id канала до старта
    await resolve_channel_id(application)

    # Запускаем webhook-сервер PTB (aiohttp)
    # Здесь укажем путь /telegram/<token>, а внешний URL = PUBLIC_URL + тот же путь.
    webhook_path = f"/telegram/{TOKEN}"
    webhook_url = f"{PUBLIC_URL.rstrip('/')}{webhook_path}"

    LOG.info("Запускаем webhook: %s", webhook_url)
    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=webhook_url,
        allowed_updates=allowed_updates,
        stop_signals=None,  # корректно завершаем через event-loop
    )

def start_bot_in_thread():
    # Отдельный поток с собственным event loop, чтобы не конфликтовать с Flask.
    def _runner():
        asyncio.run(bot_runner())
    Thread(target=_runner, daemon=True).start()

# ------------------ entrypoint ------------------

if __name__ == "__main__":
    # Поднимаем бота в фоне
    start_bot_in_thread()
    LOG.info("⚙️ Flask запущен на порту %s (keep-alive + healthz)...", PORT)
    # Запускаем Flask (он отдаёт / и /healthz; PTB слушает /telegram/<token>)
    flask_app.run(host="0.0.0.0", port=PORT)
