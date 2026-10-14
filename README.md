# GGSel Seller Alert Bot

Telegram bot that watches the public GGSel seller **1348893** and notifies
subscribed chats when a new listing appears.

It reads the seller's listings from GGSel's public search API
(`POST https://api.ggsel.com/elastic/goods/sellers`) — no HTML scraping, so the
Qrator anti-bot protection is not an issue.

## How it runs

The bot is designed to run **one pass per invocation** and exit, which fits
GitHub Actions:

- `.github/workflows/ggsel-alert.yml` starts it on a schedule (default: every
  10 minutes) and on manual dispatch.
- Each run fetches all current listings of the seller, compares them with the
  previously seen ids, sends alerts for new ones, then exits.
- The state (`state.json`) is kept between runs with the Actions cache, so the
  subscriber chat ids and the known listings are not lost.

`/start` still works: on every run the bot also reads pending Telegram updates,
so sending `/start` subscribes your chat (it may be picked up on the next run).

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Put the token into the repository secret **BOT_TOKEN**
   (Settings → Secrets and variables → Actions).
3. Open your bot in Telegram and send `/start`.
4. Wait for the next scheduled run (or run the workflow manually from the
   Actions tab).

## Running locally

1. Install Python 3.11 or newer.
2. Create a virtual environment (optional): `py -m venv .venv` + activate.
3. Install dependencies: `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and set `BOT_TOKEN`.
5. Run a single pass: `python bot.py`.

## Commands

- `/start` — subscribe this Telegram chat to alerts.
- `/status` — show how many listings and subscribers are known.
- `/stop` — unsubscribe.

## Configuration

All settings come from environment variables (see `.env.example`):

- `BOT_TOKEN` — Telegram bot token.
- `SELLER_ID` — numeric GGSel seller id (default `1348893`).
- `SELLER_URL` — seller profile URL used in messages.
- `CURRENCY` — price currency for the API (default `RUB`).
- `GGSEL_LANG` — language for the API (default `ru`).

## Limits

- GitHub's shortest cron interval is 5 minutes and runs may be delayed by a few
  minutes under load.
- The API returns at most 60 listings per page; the bot paginates up to 30 pages
  (1800 listings) per run.
- The first successful run saves a baseline and does not notify about listings
  that were already there.
