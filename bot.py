import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SELLER_ID = os.getenv("SELLER_ID", "1348893").strip()
SELLER_URL = os.getenv(
    "SELLER_URL", f"https://ggsel.net/sellers/{SELLER_ID}"
).strip()
CURRENCY = os.getenv("CURRENCY", "RUB").strip()
LANG = os.getenv("LANG", "ru").strip()
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

API_URL = "https://api.ggsel.com/elastic/goods/sellers"
PRODUCT_URL_BASE = "https://ggsel.net/catalog/product/"
PAGE_LIMIT = 60
MAX_PAGES = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Origin": "https://ggsel.net",
    "Referer": f"{SELLER_URL}",
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


async def fetch_products(session):
    """Return {product_id: {name, url, price}} for every listing of the seller."""
    products = {}
    search_after = []
    for _ in range(MAX_PAGES):
        body = {
            "id_seller": int(SELLER_ID),
            "limit": PAGE_LIMIT,
            "currency": CURRENCY,
            "lang": LANG,
            "with_filters": False,
            "sort": "sortByRec",
            "search_after": search_after,
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with session.post(
            API_URL, json=body, headers=HEADERS, timeout=timeout
        ) as response:
            if response.status != 200:
                text = await response.text()
                log.error("GGSel API %s: %s", response.status, text[:500])
            response.raise_for_status()
            payload = await response.json()

        items = payload.get("data", {}).get("items", [])
        for item in items:
            product_id = item.get("id_goods")
            if product_id is None:
                continue
            products[str(product_id)] = {
                "name": (item.get("name") or "").strip(),
                "url": PRODUCT_URL_BASE + (item.get("url") or ""),
                "price": item.get("price_wmr"),
                "unit": item.get("unit_name") or "",
            }

        if len(items) < PAGE_LIMIT:
            break
        search_after = items[-1].get("sort") or []
        if not search_after:
            break

    return products


async def tg(session, method, **payload):
    url = f"{API_BASE}/{method}"
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with session.post(url, data=payload, timeout=timeout) as response:
            return await response.json()
    except Exception as exc:
        log.warning("Telegram %s failed: %s", method, exc)
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
                    f"Готово! Буду присылать новые товары продавца GGSel №{SELLER_ID}.",
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


def format_price(price):
    if price is None:
        return "цена не указана"
    try:
        return f"{float(price):.2f} ₽"
    except (TypeError, ValueError):
        return str(price)


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
            new_ids = sorted(current - known)
            for product_id in new_ids:
                product = products[product_id]
                message = (
                    f"🔔 Новый товар у продавца GGSel №{SELLER_ID}!\n\n"
                    f"📦 {product['name']}\n"
                    f"💰 {format_price(product['price'])}\n"
                    f"🔗 {product['url']}\n\n"
                    f"Профиль продавца: {SELLER_URL}"
                )
                for chat_id in state["chat_ids"]:
                    await send_message(session, chat_id, message)
            state["known_products"] = sorted(known | current)
            if new_ids:
                log.info("Found %d new product links", len(new_ids))

        save_state(state)


if __name__ == "__main__":
    asyncio.run(main())
