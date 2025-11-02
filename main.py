# main.py
import os, re, sys, asyncio, logging, threading
from flask import Flask, request, abort
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
SECRET = os.getenv("WEBHOOK_SECRET", os.urandom(16).hex())
PORT = int(os.getenv("PORT", "10000"))
DELETE_ALL = os.getenv("DELETE_ALL", "false").lower() == "true"
BANNED_RAW = os.getenv("BANNED", "casino, http://, https://, t.me/")
BANNED = [re.compile(re.escape(w.strip()), re.I) for w in BANNED_RAW.split(",") if w.strip()]

if not TOKEN or not BASE_URL:
    print("❌ Set BOT_TOKEN and BASE_URL"); sys.exit(1)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("BlajeChatBot")

flask_app = Flask(__name__)

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
application = Application.builder().token(TOKEN).build()

def is_banned(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in BANNED)

async def try_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, reason: str):
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
        log.info("🗑 deleted %s in %s (%s)", msg_id, chat_id, reason)
    except Exception as e:
        log.warning("delete failed %s/%s: %s", chat_id, msg_id, e)

async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    if DELETE_ALL: return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")
    if is_banned((m.text or m.caption or "")[:4096]):
        await try_delete(context, m.chat_id, m.message_id, "banned_text")

async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    if DELETE_ALL: return await try_delete(context, m.chat_id, m.message_id, "DELETE_ALL")
    if is_banned((m.text or m.caption or "")[:4096]):
        await try_delete(context, m.chat_id, m.message_id, "banned_text")

# группы/супергруппы
application.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, on_group_message))
# каналы (замена ChannelPostHandler)
application.add_handler(MessageHandler(filters.ChatType.CHANNEL & ~filters.StatusUpdate.ALL, on_channel_post))

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL  = f"{BASE_URL}{WEBHOOK_PATH}"

async def setup_webhook():
    await application.initialize()
    await application.start()
    try: await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e: log.warning("delete_webhook warn: %s", e)
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET,
        allowed_updates=[
            "message","edited_message",
            "channel_post","edited_channel_post",
            "chat_member","my_chat_member"
        ],
        max_connections=40,
    )
    log.info("✅ webhook set: %s", WEBHOOK_URL)

def start_bot_async():
    fut = asyncio.run_coroutine_threadsafe(setup_webhook(), loop)
    fut.result(timeout=30)

start_bot_async()

@flask_app.get("/")
def index():
    return {"ok": True, "service": "BlajeChatBot", "webhook": WEBHOOK_URL}

@flask_app.post(WEBHOOK_PATH)
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
        abort(403)
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        application.update_queue.put_nowait(update)
    except Exception as e:
        log.exception("webhook error: %s", e)
    return "ok", 200

@flask_app.get("/healthz")
def healthz():
    return {"status": "ok"}

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
