"""Shared helpers for the Phase 1 autopilot (stdlib only).

Paths, config, the append-only event log, Telegram/macOS notifications.
Secrets (the Telegram bot token) are read from a file outside the repo and
are never printed or logged.
"""
from __future__ import annotations

import contextlib
import csv
import fcntl
import datetime as dt
import json
import re
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "autopilot"
STATE = AUTOPILOT / "state"
LOGS = AUTOPILOT / "logs"
REPORTS = AUTOPILOT / "reports"
EVENTS = LOGS / "events.jsonl"
SEEN = ROOT / "job_scraper" / "seen_jobs.json"
TRACKER = ROOT / "job_search_tracker.csv"
APPS = ROOT / "documents" / "applications"

for _d in (STATE, LOGS, REPORTS):
    _d.mkdir(parents=True, exist_ok=True)


def config() -> dict:
    return json.loads((AUTOPILOT / "config.json").read_text(encoding="utf-8"))


def _company_norm(name: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def excluded_company(name: str) -> str | None:
    """Reason if the company is permanently excluded (config excluded_companies), else None."""
    n = _company_norm(name)
    for comp, reason in config().get("excluded_companies", {}).items():
        c = _company_norm(comp)
        if n and (n == c or n.startswith(c + " ") or n.replace(" ", "") == c.replace(" ", "")):
            return reason
    return None


def tz() -> ZoneInfo:
    return ZoneInfo(config().get("timezone", "Europe/Istanbul"))


def now() -> dt.datetime:
    return dt.datetime.now(tz())


def log_event(kind: str, **fields) -> None:
    """Append one event to logs/events.jsonl (the audit trail)."""
    record = {"ts": now().isoformat(timespec="seconds"), "type": kind, **fields}
    with EVENTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_events(since: dt.datetime | None = None) -> list[dict]:
    if not EVENTS.exists():
        return []
    out = []
    for line in EVENTS.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is not None:
            try:
                if dt.datetime.fromisoformat(ev["ts"]) < since:
                    continue
            except (KeyError, ValueError):
                continue
        out.append(ev)
    return out


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def seen_jobs() -> dict:
    return load_json(SEEN, {"seen": {}}).get("seen", {})


def tracker_rows() -> list[dict]:
    if not TRACKER.exists():
        return []
    with TRACKER.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@contextlib.contextmanager
def single_instance(name: str):
    """Hold an exclusive lock for one step; yields False if another run holds it."""
    fh = (STATE / f"{name}.lock").open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


# --- notifications ------------------------------------------------------------

def _telegram_env() -> dict:
    path = Path(os.path.expanduser(config()["notify"]["telegram_env_file"]))
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def telegram_configured() -> bool:
    env = _telegram_env()
    return bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> tuple[bool, str]:
    """Send plain text to the candidate's own chat. Returns (ok, reason).

    The reason never contains the token.
    """
    env = _telegram_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False, "telegram not configured"
    ok_all = True
    for chunk in [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]:
        data = urllib.parse.urlencode(
            {"chat_id": chat, "text": chunk, "disable_web_page_preview": "true"}
        ).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ok_all = ok_all and json.loads(resp.read()).get("ok", False)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return False, f"telegram error: {type(exc).__name__}"
    return ok_all, "sent" if ok_all else "telegram returned not ok"


def send_telegram_ids(text: str) -> tuple[list[int], str]:
    """Like send_telegram, but returns the Telegram message ids (to bind replies to a list)."""
    env = _telegram_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return [], "telegram not configured"
    ids = []
    for chunk in [text[i:i + 3800] for i in range(0, len(text), 3800)] or [""]:
        data = urllib.parse.urlencode({"chat_id": chat, "text": chunk, "disable_web_page_preview": "true"}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=20) as resp:
                r = json.loads(resp.read())
            if r.get("ok"):
                ids.append(r["result"]["message_id"])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return ids, f"telegram error: {type(exc).__name__}"
    return ids, "sent" if ids else "telegram returned not ok"


def mac_notify(title: str, body: str) -> None:
    safe = lambda s: s.replace("\\", " ").replace('"', "'")[:180]
    script = f'display notification "{safe(body)}" with title "{safe(title)}"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


PENDING = STATE / "pending_alerts.json"


def flush_pending() -> int:
    """Resend alerts that failed earlier (e.g. Mac was offline). Returns how many were sent."""
    queue = load_json(PENDING, [])
    if not queue:
        return 0
    left, sent = [], 0
    for item in queue:
        ok, _ = send_telegram(item["text"])
        if ok:
            sent += 1
        else:
            left.append(item)
    save_json(PENDING, left)
    return sent


def notify(title: str, text: str) -> str:
    """Telegram first; on failure queue for retry and show a macOS notification."""
    flush_pending()
    message = f"{title}\n\n{text}"
    ok, reason = send_telegram(message)
    if ok:
        return "telegram"
    if telegram_configured():
        queue = load_json(PENDING, [])
        queue.append({"ts": now().isoformat(timespec="seconds"), "text": message})
        save_json(PENDING, queue[-50:])
    if config()["notify"].get("mac_notification_fallback", True):
        mac_notify(title, text.splitlines()[0] if text else title)
        return f"mac ({reason})"
    return f"none ({reason})"
