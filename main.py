# main.py — BlajeChatBot на Render
# Удаляет сообщения "от имени канала" и отправляет предупреждение.
# Архитектура: один Flask-сервер принимает вебхук, PTB обрабатывает апдейты.
# Требуется: python-telegram-bot==21.8, Flask

import os
import asyncio
import logging
import threading

from flask import Flask, request, abort, jsonify
from telegram import Update
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# -------------------- Логирование --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("BlajeChatBot")

# -------------------- Переменные окружения --------------------
TOKEN = os.environ.get("TOKEN")                      # ОБЯЗАТЕЛЬНО (верхний регистр!)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")  # https://<your>.onrender.com
PORT = int(os.environ.get("PORT", "1000"))           # Render сам подставляет

if not TOKEN:
    raise RuntimeError("❌ Переменная окружения TOKEN не найдена.")

WEBHOOK_PATH = f"/telegram/{TOKEN}"   # уникальный путь вебхука
WEBHOOK_URL = f"{PUBLIC_URL}{WEBHOOK_PATH}"

WARNING_TEXT = (
    "⚠️ Сообщения, отправленные *от имени канала*, запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего профиля.\n— Бот-модератор."
)

# -------------------- Flask (один-единственный HTTP-сервер) --------------------
flask_app = Flask(__name__)

@flask_app.get("/")
def index():
    return "BlajeChatBot: OK"

@flask_app.get("/health")
def health():
    return jsonify(ok=True)

# PTB объекты будут проинициализированы позже
application: Application | None = None
main_loop: asyncio.AbstractEventLoop | None = None

@flask_app.post(WEBHOOK_PATH)
def telegram_webhook():
    """Единственная точка входа для апдейтов от Telegram."""
    global application, main_loop

    if application is None or main_loop is None:
        abort(503)  # ещё не готово

    if request.headers.get("content-type") != "application/json":
        abort(415)

    data = request.get_data(as_text=True)
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
    except Exception as e:
        log.warning("Не удалось распарсить апдейт: %s", e)
        abort(400)

    # Кормим PTB-Application апдейтом в его event loop из Flask-потока
    fut = asyncio.run_coroutine_threadsafe(application.process_update(update), main_loop)
    try:
        fut.result(timeout=10)
    except Exception as e:
        log.warning("Ошибка обработки апдейта: %s", e)

    return "OK"

# -------------------- Telegram handlers --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text(
            "✅ Бот запущен.\n"
            "Добавьте меня *администратором* группы (с правом *удалять сообщения*), "
            "и я буду удалять посты, отправленные от имени канала.",
            parse_mode="Markdown"
        )

def is_channel_identity(update: Update) -> bool:
    msg = update.effective_message
    # sender_chat != None — сообщение отправлено от имени канала/сообщества
    return bool(msg and msg.sender_chat)

async def guard_channel_identity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat

    if not is_channel_identity(update):
        return

    # Пытаемся удалить исходное
    try:
        await msg.delete()
        log.info("🗑️ Удалено сообщение от имени канала в чате %s", chat.id)
    except Forbidden:
        log.warning("❌ Нет прав на удаление в чате %s. Дайте боту право 'Удалять сообщения'.", chat.id)
        return
    except BadRequest as e:
        log.warning("⚠️ BadRequest при удалении: %s", e)
        return

    # Пишем предупреждение (учитываем топики)
    try:
        if msg.is_topic_message and msg.message_thread_id:
            await context.bot.send_message(
                chat_id=chat.id,
                text=WARNING_TEXT,
                parse_mode="Markdown",
                message_thread_id=msg.message_thread_id
            )
        else:
            await context.bot.send_message(
                chat_id=chat.id,
                text=WARNING_TEXT,
                parse_mode="Markdown"
            )
    except Exception as e:
        log.warning("Не удалось отправить предупреждение: %s", e)

# -------------------- Инициализация Telegram без своего веб-сервера --------------------
async def init_telegram_app() -> Application:
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", cmd_start))
    # Ловим сообщения в группах, отправленные от имени канала
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.SenderChat(True), guard_channel_identity))

    # ВАЖНО: никакого run_webhook / run_polling!
    await app.initialize()
    await app.start()

    # Настраиваем вебхук на наш Flask-эндпоинт
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_webhook(url=WEBHOOK_URL)
    log.info("🌐 Webhook установлен: %s", WEBHOOK_URL)

    return app

# -------------------- Старт всего сервиса --------------------
def start_flask_in_thread():
    # Поднимаем Flask в отдельном потоке (один HTTP-сервер на Render)
    th = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True
    )
    th.start()
    log.info("🚀 Flask запущен на порту %s", PORT)
    return th

def main():
    global application, main_loop

    # 1) Запускаем Flask
    start_flask_in_thread()

    # 2) Создаём и фиксируем главный event loop для PTB
    main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(main_loop)

    # 3) Инициализируем Telegram Application (без собственного сервера)
    application = main_loop.run_until_complete(init_telegram_app())

    log.info("✅ Сервис поднят. Готов принимать апдейты.")

    # 4) Держим процесс (Render ожидает, что процесс будет жить)
    try:
        main_loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if application is not None:
            main_loop.run_until_complete(application.stop())
            main_loop.run_until_complete(application.shutdown())
        main_loop.close()

if __name__ == "__main__":
    main()
