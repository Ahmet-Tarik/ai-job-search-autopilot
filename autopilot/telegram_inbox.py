#!/usr/bin/env python3
"""Read the candidate's Telegram replies to the daily shortlist (no LLM). Run by launchd every 5 min.

Accepted only from the linked private chat (TELEGRAM_CHAT_ID); everything else
is ignored. Commands (case-insensitive, Turkish words accepted too):
  apply to 1, 3, 5   /  apply 1 3   /  başvur 1,3    -> select shortlist items for preparation
  skip 2                                            -> mark an item as not wanted
  list                                              -> resend today's shortlist
A selection authorizes PREPARATION (tailored CV, cover letter, form plan,
packet) of exactly those items. Submitting still requires the candidate's
explicit message in a Claude chat ("submit packets ...").

State: state/telegram_offset.json, state/selections.json.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import STATE, _telegram_env, excluded_company, load_json, log_event, now, save_json, send_telegram, single_instance  # noqa: E402
import shortlist  # noqa: E402

OFFSET = STATE / "telegram_offset.json"
SELECTIONS = STATE / "selections.json"


def get_updates(token: str, offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode({"offset": offset, "timeout": 0})
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read()).get("result", [])


def parse(text: str) -> tuple[str, list[int]] | None:
    t = text.strip().lower()
    nums = [int(n) for n in re.findall(r"\d+", t)]
    if re.match(r"^(apply|başvur|basvur|select|seç|sec)\b", t) and nums:
        return "apply", nums
    if re.match(r"^(skip|geç|gec|no|hayır|hayir)\b", t) and nums:
        return "skip", nums
    if re.match(r"^(list|liste|shortlist)\b", t):
        return "list", []
    return None


def handle(cmd: str, nums: list[int], reply_to: int | None = None) -> str:
    sl = shortlist.load_published(message_id=reply_to)
    if cmd == "list":
        return shortlist.render(sl) if sl else "No shortlist yet today."
    if not sl:
        return "There is no shortlist to select from yet."
    by_n = {it["n"]: it for it in sl["items"]}
    sel = load_json(SELECTIONS, [])
    done, bad = [], []
    for n in nums:
        it = by_n.get(n)
        if not it:
            bad.append(str(n))
            continue
        if cmd == "apply" and excluded_company(it["company"]):
            done.append(f"#{n} {it['company']} (permanently excluded - not prepared)")
            continue
        if any(s["key"] == it["key"] and s["status"] in {"selected", "preparing", "prepared"} for s in sel) and cmd == "apply":
            done.append(f"#{n} {it['company']} (already selected)")
            continue
        sel.append({"key": it["key"], "n": n, "list_id": sl["list_id"], "list_date": sl["date"], "company": it["company"], "title": it["title"],
                    "url": it["url"], "category": it["category"], "status": "selected" if cmd == "apply" else "skipped",
                    "ts": now().isoformat(timespec="seconds")})
        done.append(f"#{n} {it['title']} - {it['company']}")
        log_event("shortlist_selected" if cmd == "apply" else "shortlist_skipped", key=it["key"], company=it["company"], n=n)
    save_json(SELECTIONS, sel)
    if cmd == "skip":
        return "Skipped: " + ", ".join(done) if done else "Nothing skipped."
    msg = [f"Selected for preparation (list {sl['list_id']}):"] + [f"- {d}" for d in done]
    if bad:
        msg.append(f"Not on the list: {', '.join(bad)}")
    msg.append("The next AI run (about 07:48 or 13:55, or now if you say 'prepare my selections' in Claude) "
               "tailors the CV, cover letter and form plan. Submission needs your OK in Claude: 'submit packets ...'.")
    return "\n".join(msg)


def main() -> int:
    env = _telegram_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return 0
    with single_instance("telegram_inbox") as ok:
        if not ok:
            return 0
        state = load_json(OFFSET, {"offset": 0})
        try:
            updates = get_updates(token, state["offset"])
        except Exception as exc:  # noqa: BLE001 - offline etc.; try again next run
            print(f"telegram unreachable: {type(exc).__name__}")
            return 0
        for u in updates:
            state["offset"] = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(chat) or msg.get("chat", {}).get("type") != "private":
                continue  # only the candidate's own chat counts
            parsed = parse(msg.get("text") or "")
            if not parsed:
                continue
            reply_to = (msg.get("reply_to_message") or {}).get("message_id")
            reply = handle(*parsed, reply_to=reply_to)
            send_telegram(reply)
        save_json(OFFSET, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
