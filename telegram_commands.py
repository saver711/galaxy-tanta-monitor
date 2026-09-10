#!/usr/bin/env python3
"""Handle /check sent to the Telegram bot.

This workflow is intentionally simple: GitHub Actions polls Telegram every
5 minutes. A /check message from the configured chat triggers a live check.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

TIMEOUT = int(os.getenv("TIMEOUT", "30"))
STATE_FILE = Path(os.getenv("COMMAND_STATE_FILE", "telegram_state.json"))


def api(method, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["result"]


def send_message(chat_id, text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=TIMEOUT)
    r.raise_for_status()


def load_offset():
    if STATE_FILE.exists():
        try:
            return int(json.loads(STATE_FILE.read_text()).get("offset", 0))
        except Exception:
            pass
    return 0


def save_offset(offset):
    STATE_FILE.write_text(json.dumps({"offset": offset}, indent=2) + "\n")


def main():
    allowed_chat = str(os.environ["TELEGRAM_CHAT_ID"])
    offset = load_offset()
    updates = api("getUpdates", offset=offset, timeout=0, allowed_updates=json.dumps(["message"]))

    next_offset = offset
    for update in updates:
        next_offset = max(next_offset, int(update["update_id"]) + 1)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip().split()[0].lower() if message.get("text") else ""

        if chat_id != allowed_chat:
            continue

        if text in {"/check", "/check@galaxytantaalertsbot"}:
            send_message(chat_id, "🔎 Checking Galaxy Tanta now…")
            result = subprocess.run([sys.executable, "monitor.py", "--report"], capture_output=True, text=True)
            if result.returncode != 0:
                send_message(chat_id, "❌ The check failed. See the GitHub Actions run for details.")
                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                raise SystemExit(result.returncode)
        elif text in {"/start", "/help"}:
            send_message(chat_id, "👋 Galaxy Tanta monitor is ready.\n\nSend /check anytime to check What's On now.")

    save_offset(next_offset)
    print(f"Processed {len(updates)} Telegram update(s). Next offset: {next_offset}")


if __name__ == "__main__":
    main()
