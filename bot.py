import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SELLER_URL = os.getenv(
    "SELLER_URL", "https://ggsel.net/sellers/1348893"
).strip()
CHECK_INTERVAL = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "90")))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
PRODUCT_HINTS = ("/item/", "/product/", "/goods/", "/lot/", "/catalog/")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.8",
}


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"known_products": [], "chat_ids": [], "initialized": False}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_products(html, base_url):
    """Best-effort parser for product links rendered in the seller page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = {}
    seller_path = urlparse(SELLER_URL).path.rstrip("/")

    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].strip())
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or parsed.netloc != urlparse(base_url).netloc:
            continue

        path = parsed.path.rstrip("/")
        if not path or path == seller_path or "/sellers/" in path:
            continue

        text = " ".join(anchor.get_text(" ", strip=True).split())
        if not text or len(text) < 3:
            continue

        lowered = path.lower()
        has_product_hint = any(hint in lowered for hint in PRODUCT_HINTS)
        has_id = bool(re.search(r"(?:id=|/)[0-9]{4,}", href))
        # Ignore navigation links; only keep links that resemble listing URLs.
        if not (has_product_hint or has_id):
            continue

        key = href.split("#", 1)[0].rstrip("/")
        products[key] = {"url": key, "title": text[:180]}

    return products


async def fetch_products(session):
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.get(SELLER_URL, headers=HEADERS, timeout=timeout) as response:
        response.raise_for_status()
        html = await response.text()
    return extract_products(html, SELLER_URL)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return
    state = load_state()
    chat_id = update.effective_chat.id
    if chat_id not in state["chat_ids"]:
        state["chat_ids"].append(chat_id)
        save_state(state)
    await update.effective_message.reply_text(
        "Готово! Буду отслеживать новые товары продавца GGSel №1348893.\n"
        f"Проверка примерно раз в {CHECK_INTERVAL} сек.\n"
        "Команды: /status — состояние, /check — проверить сейчас."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.effective_message.reply_text(
        f"Продавец: {SELLER_URL}\n"
        f"Интервал: {CHECK_INTERVAL} сек.\n"
        f"Известных товаров: {len(state['known_products'])}\n"
        f"Первичная проверка выполнена: {'да' if state['initialized'] else 'нет'}"
    )


async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Проверяю страницу продавца…")
    try:
        async with aiohttp.ClientSession() as session:
            products = await fetch_products(session)
        await update.effective_message.reply_text(
            f"В HTML страницы найдено ссылок, похожих на товары: {len(products)}.\n"
            "Если на странице товары отображаются в браузере, но здесь их 0, "
            "возможно, сайт загружает их через JavaScript — тогда потребуется "
            "подстроить способ получения данных."
        )
    except Exception as exc:
        log.exception("Manual check failed")
        await update.effective_message.reply_text(
            f"Не удалось проверить GGSel: {type(exc).__name__}: {exc}"
        )


async def monitor(app: Application):
    await asyncio.sleep(5)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                products = await fetch_products(session)

            state = load_state()
            current = set(products)
            known = set(state["known_products"])

            # On first successful run, save the current page as baseline to avoid
            # sending notifications for listings that were already there.
            if not state["initialized"]:
                state["known_products"] = sorted(current)
                state["initialized"] = True
                save_state(state)
                log.info("Initial baseline saved: %d product links", len(current))
            else:
                new_keys = current - known
                if new_keys:
                    for key in sorted(new_keys):
                        product = products[key]
                        message = (
                            "🔔 Новый товар у продавца GGSel №1348893!\n\n"
                            f"📦 {product['title']}\n"
                            f"🔗 {product['url']}\n\n"
                            f"Профиль продавца: {SELLER_URL}"
                        )
                        for chat_id in load_state()["chat_ids"]:
                            try:
                                await app.bot.send_message(chat_id=chat_id, text=message)
                            except Exception:
                                log.exception("Could not notify chat %s", chat_id)
                    state["known_products"] = sorted(known | current)
                    save_state(state)
                    log.info("Found %d new product links", len(new_keys))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Seller check failed")

        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(app: Application):
    app.create_task(monitor(app), name="ggsel-seller-monitor")


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is missing. Copy .env.example to .env and set your Telegram bot token."
        )
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("check", check_now))
    log.info("Bot started; seller page: %s", SELLER_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
