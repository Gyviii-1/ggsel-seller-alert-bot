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
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    state.setdefault("known_products", [])
    state.setdefault("chat_ids", [])
    state.setdefault("initialized", False)
    state.setdefault("last_update_id", 0)
    return state


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
        if (
            parsed.scheme not in ("http", "https")
            or parsed.netloc != urlparse(base_url).netloc
        ):
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


async def tg(session, method, **payload):
    url = f"{API_BASE}/{method}"
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.post(url, data=payload, timeout=timeout) as response:
        try:
            return await response.json()
        except Exception:
            return {"ok": False}


async def send_message(session, chat_id, text):
    await tg(session, "sendMessage", chat_id=chat_id, text=text)


async def collect_updates(session, state):
    """Process pending Telegram updates so /start works in one-shot mode."""
    offset = int(state.get("last_update_id", 0)) + 1
    data = await tg(session, "getUpdates", offset=offset, timeout=0)
    if not isinstance(data, dict) or not data.get("ok"):
        return
    for update in data.get("result", []):
        state["last_update_id"] = max(
            int(state["last_update_id"]), int(update["update_id"])
        )
        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is None:
            continue

        if text.startswith("/start"):
            if chat_id not in state["chat_ids"]:
                state["chat_ids"].append(chat_id)
                await send_message(
                    session,
                    chat_id,
                    "Готово! Буду присылать новые товары продавца GGSel №1348893.",
                )
            else:
                await send_message(session, chat_id, "Ты уже подписан на алерты.")
        elif text.startswith("/status"):
            await send_message(
                session,
                chat_id,
                f"Продавец: {SELLER_URL}\n"
                f"Известных товаров: {len(state['known_products'])}\n"
                f"Подписчиков: {len(state['chat_ids'])}\n"
                f"База собрана: {'да' if state['initialized'] else 'нет'}",
            )
        elif text.startswith("/stop"):
            if chat_id in state["chat_ids"]:
                state["chat_ids"].remove(chat_id)
                await send_message(session, chat_id, "Отписал от алертов.")


async def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN отсутствует. Задай переменную окружения BOT_TOKEN."
        )

    state = load_state()
    async with aiohttp.ClientSession() as session:
        await collect_updates(session, state)

        products = await fetch_products(session)
        current = set(products)

        # On the first successful run, save the current page as baseline so we
        # do not notify about listings that were already there.
        if not state["initialized"]:
            state["known_products"] = sorted(current)
            state["initialized"] = True
            log.info("Initial baseline saved: %d product links", len(current))
        else:
            known = set(state["known_products"])
            new_keys = sorted(current - known)
            for key in new_keys:
                product = products[key]
                message = (
                    "🔔 Новый товар у продавца GGSel №1348893!\n\n"
                    f"📦 {product['title']}\n"
                    f"🔗 {product['url']}\n\n"
                    f"Профиль продавца: {SELLER_URL}"
                )
                for chat_id in state["chat_ids"]:
                    await send_message(session, chat_id, message)
            state["known_products"] = sorted(known | current)
            if new_keys:
                log.info("Found %d new product links", len(new_keys))

        save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
