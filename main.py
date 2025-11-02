# main.py — BlajeChatBot (Бот Модератор)
# Хостинг: Render.com (webhook), PORT берется из ENV
# Требуется: python-telegram-bot==21.8
#
# Поведение:
#  - Удаляет сообщения "от имени канала" в группах/супергруппах
#  - Исключение: можно писать от канала @blajeru (https://t.me/blajeru)
#  - Пишет предупреждение в общий чат и в комментарии (топик) к посту
#  - Антиспам (кулдаун 2 сек) на предупреждения
#  - Эндпоинты "/" и "/health" (через встроенный aiohttp PTB)
#
# Дополнительно можно разрешить ещё каналы через переменную окружения:
#   ALLOWED_CHANNELS=@username1,@username2,-1001234567890
# (поддерживаются и @username, и numeric-id со знаком -100)

import asyncio
import logging
import os
import time
from typing import Dict, Iterable, Set

from aiohttp import web
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ====== Базовые настройки ======
TOKEN = os.environ["TOKEN"]                       # Render → Environment
PORT = int(os.environ.get("PORT", "10000"))       # Render автоматически выдаёт
PUBLIC_URL = (
    os.environ.get("PUBLIC_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "https://blajechatbot.onrender.com"
).rstrip("/")

# Разрешённые каналы — сюда добавляем @blajeru
# Можно расширить через ENV ALLOWED_CHANNELS (через запятую)
ALLOWED_USERNAMES_DEFAULT = {"@blajeru"}  # ← именно здесь разрешаем @blajeru
ALLOWED_SENDER_CHAT_IDS: Set[int] = set()        # заполним при старте


# Текст предупреждения
WARNING_TEXT = (
    "Сообщения от имени канала в этой группе запрещены и будут удаляться.\n"
    "Пожалуйста, пишите от своего личного профиля.\n"
    "Бот Модератор."
)

# Кулдаун (секунды) между предупреждениями в одном чате
WARN_COOLDOWN_SECONDS = 2
_last_warn_time_by_chat: Dict[int, float] = {}

# ====== Логирование ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("BlajeChatBot")
log.warning("Using PUBLIC_URL = %s", PUBLIC_URL)


def _normalize_handle(s: str) -> str:
    """Приводим к '@username' если пришло 'username'/'https://t.me/username'/'@username'."""
    s = s.strip()
    if not s:
        return s
    if s.startswith("http"):
        # https://t.me/username[/...]
        try:
            from urllib.parse import urlparse
            p = urlparse(s)
            if p.netloc.lower().endswith("t.me"):
                handle = p.path.strip("/").split("/")[0]
                return "@" + handle.lstrip("@")
        except Exception:
            return s
    if not s.startswith("@"):
        return "@" + s
    return s


def _iter_allowed_usernames() -> Iterable[str]:
    # из ENV ALLOWED_CHANNELS добавляем, если есть
    extra = os.environ.get("ALLOWED_CHANNELS", "")
    parts = [p for p in (x.strip() for x in extra.split(",")) if p]
    all_raw = set(parts) | set(ALLOWED_USERNAMES_DEFAULT)
    return {_normalize_handle(x) for x in all_raw if x}


async def resolve_allowed_ids(app: Application) -> None:
    """Разрешённые каналы могут быть указаны @username или числом. Превратим в ID."""
    usernames = set()
    numeric: Set[int] = set()

    for item in _iter_allowed_usernames():
        # Если это целое число — сразу в ID
        try:
            if item.startswith("-100") or item.startswith("-"):
                numeric.add(int(item))
                continue
        except Exception:
            pass
        # иначе это @username
        usernames.add(item)

    # Пробуем резолвить username → id
    for handle in usernames:
        try:
            chat = await app.bot.get_chat(handle)  # '@blajeru' → Chat
            ALLOWED_SENDER_CHAT_IDS.add(chat.id)
            log.info("Разрешённый канал: %s → %s", handle, chat.id)
        except Exception as e:
            log.warning("Не удалось получить id для %s: %s", handle, e)

    # Добавляем числовые id если есть
    if numeric:
        ALLOWED_SENDER_CHAT_IDS.update(numeric)
        for nid in numeric:
            log.info("Разрешённый канал (numeric): %s", nid)

    if not ALLOWED_SENDER_CHAT_IDS:
        log.warning(
            "Внимание: список разрешённых каналов пуст. "
            "По умолчанию должен быть разрешён @blajeru."
        )


# ====== Обработчик сообщений ======
async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return

    sc = msg.sender_chat
    if sc is None:
        return

    # Если канал разрешён — пропускаем без удаления
    if sc.id in ALLOWED_SENDER_CHAT_IDS:
        return

    # Удаляем сообщение от канала
    try:
        await msg.delete()
    except Exception as e:
        log.warning("Не удалось удалить сообщение: %s", e)

    # Антиспам предупреждений
    chat_id = msg.chat_id
    now = time.time()
    last = _last_warn_time_by_chat.get(chat_id, 0.0)
    if now - last < WARN_COOLDOWN_SECONDS:
        return
    _last_warn_time_by_chat[chat_id] = now

    # В общий чат
    try:
        await context.bot.send_message(chat_id=chat_id, text=WARNING_TEXT)
    except Exception as e:
        log.warning("Не удалось отправить предупреждение в чат: %s", e)

    # В комментарии (топик), если есть
    if getattr(msg, "message_thread_id", None):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=msg.message_thread_id,
                text=WARNING_TEXT,
            )
        except Exception as e:
            log.warning(
                "Не удалось отправить предупреждение в топик (%s): %s",
                msg.message_thread_id,
                e,
            )


# ====== Health-эндпоинты (через встроенный aiohttp у PTB) ======
async def health_handler(_request: web.Request) -> web.Response:
    return web.Response(text="OK: BlajeChatBot alive")

async def root_handler(_request: web.Request) -> web.Response:
    return web.Response(text="BlajeChatBot работает!")


# ====== Запуск (webhook) ======
async def main() -> None:
    log.info("🚀 BlajeChatBot (Webhook) запускается…")

    app = Application.builder().token(TOKEN).build()

    # Разрешённые каналы → id
    await resolve_allowed_ids(app)

    # Обработчик: любые сообщения в группах/супергруппах
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_all))

    # Aiohttp-маршруты
    app.web_app.add_get("/", root_handler)
    app.web_app.add_get("/health", health_handler)

    # Webhook
    await app.bot.delete_webhook(drop_pending_updates=True)
    webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"
    log.info("Ставим webhook: %s", webhook_url)

    await app.bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )

    await app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    asyncio.run(main())
