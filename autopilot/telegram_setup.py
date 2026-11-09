#!/usr/bin/env python3
"""One-time Telegram setup. Never prints or logs the bot token.

Before running:
  1. In Telegram, talk to @BotFather -> /newbot -> copy the token.
  2. In Terminal (so the token never passes through Claude):
       mkdir -p ~/.config/job-search && chmod 700 ~/.config/job-search
       printf 'TELEGRAM_BOT_TOKEN=%s\n' 'PASTE_TOKEN_HERE' > ~/.config/job-search/telegram.env
       chmod 600 ~/.config/job-search/telegram.env
  3. Open your new bot in Telegram and send it /start.

Then: python3 autopilot/telegram_setup.py
It finds your private chat id from the bot's updates, stores it in the same
file and sends a test message. Only private chats are accepted.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import config, log_event, send_telegram  # noqa: E402


def api(token: str, method: str) -> dict:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=20) as r:
        return json.loads(r.read())


def main() -> int:
    path = Path(os.path.expanduser(config()["notify"]["telegram_env_file"]))
    if not path.exists():
        print(f"Missing {path}. Create it first (see the instructions at the top of this file).")
        return 1
    if oct(path.stat().st_mode)[-3:] != "600":
        print(f"Warning: {path} should be chmod 600.")
    lines = path.read_text(encoding="utf-8").splitlines()
    env = dict(l.split("=", 1) for l in lines if "=" in l and not l.startswith("#"))
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip().strip("'\"")
    if not token:
        print("TELEGRAM_BOT_TOKEN is empty in the env file.")
        return 1
    try:
        me = api(token, "getMe")
    except Exception as exc:  # noqa: BLE001 - report the type only, never the URL (it holds the token)
        print(f"Token check failed ({type(exc).__name__}). Is the token correct?")
        return 1
    print(f"Bot OK: @{me['result']['username']}")
    updates = api(token, "getUpdates").get("result", [])
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat", {})
        if chat.get("type") == "private":
            chats[chat["id"]] = chat.get("first_name", "")
    if len(chats) != 1:
        print(f"Found {len(chats)} private chats. Send /start to your bot from your own account "
              "(and only yours), then run this again.")
        return 1
    chat_id, name = next(iter(chats.items()))
    kept = [l for l in lines if not l.startswith("TELEGRAM_CHAT_ID=")]
    path.write_text("\n".join(kept + [f"TELEGRAM_CHAT_ID={chat_id}"]) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    ok, reason = send_telegram("Job-search autopilot connected. You will get briefings at 09:00 and 22:00 (Istanbul time).")
    print(f"Chat linked ({name}). Test message: {reason}")
    log_event("telegram_configured", ok=ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
