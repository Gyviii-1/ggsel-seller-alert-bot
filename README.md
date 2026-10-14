# GGSel Seller Alert Bot

Telegram bot that checks the public GGSel profile for seller **1348893** and notifies subscribed chats when it detects a new product link.

## Setup (Windows)

1. Install Python 3.11 or newer.
2. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
3. Download this repository and open a terminal in its folder.
4. Create a virtual environment (optional but recommended):
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
5. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
6. Copy `.env.example` to `.env` and put the token in `BOT_TOKEN`.
7. Start the bot:
   ```powershell
   python bot.py
   ```
8. Open your bot in Telegram and send `/start`.

## Commands

- `/start` — subscribe this Telegram chat to alerts.
- `/status` — show current monitor status.
- `/check` — manually test whether product-like links are visible in the page HTML.

## Important limitations

- This is a first-pass monitor, not a guarantee of instant detection. It checks the public seller page at an interval of at least 60 seconds.
- The parser can only detect product links present in the HTML returned to it. If GGSel renders the product list using JavaScript or changes its page structure, the parser must be adapted to the site's public data endpoint or markup.
- The first successful scan saves a baseline and does not notify about links already present.
- Keep `.env` and `state.json` private. Never commit your real bot token.
- Respect GGSel's terms and avoid aggressive polling. Increase the interval if the site rate-limits requests.
