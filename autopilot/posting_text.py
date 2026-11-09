"""Fetch and cache a posting's cleaned text (for the local model and benchmarks).

Order: portal CLI detail (LinkedIn), public ATS API (Lever, Greenhouse), then
tools/fetch_page.py (robots.txt enforced, no header spoofing beyond that tool).
Every body passes tools/posting_guard.py. Cached in state/postings/<key>.txt.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from common import ROOT, STATE

CACHE = STATE / "postings"
CACHE.mkdir(parents=True, exist_ok=True)
BUN = "/opt/homebrew/bin/bun"


def _guard_text(text: str) -> str:
    p = subprocess.run([sys.executable, str(ROOT / "tools/posting_guard.py"), "--text", "--json"],
                       input=text, capture_output=True, text=True, timeout=60)
    out = p.stdout
    return out.split("--- text ---", 1)[1].strip() if "--- text ---" in out else text


def _guard_html_file(path: Path) -> str:
    p = subprocess.run([sys.executable, str(ROOT / "tools/posting_guard.py"), "--in", str(path), "--html", "--json"],
                       capture_output=True, text=True, timeout=60)
    out = p.stdout
    return out.split("--- text ---", 1)[1].strip() if "--- text ---" in out else ""


def fetch(key: str, url: str) -> str | None:
    cached = CACHE / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)[:120]}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    text = None
    try:
        if "linkedin.com" in url:
            p = subprocess.run([BUN, "run", str(ROOT / ".agents/skills/linkedin-search/cli/src/cli.ts"), "detail", url,
                                "--format", "plain"], capture_output=True, text=True, timeout=90, cwd=ROOT)
            if p.returncode == 0 and p.stdout.strip():
                text = _guard_text(p.stdout)
        elif m := re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url):
            with urllib.request.urlopen(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}?mode=json", timeout=20) as r:
                d = json.loads(r.read())
            text = _guard_text(f"{d.get('text')}\n{d.get('descriptionPlain', '')}\n" +
                               "\n".join(f"{l.get('text')}: {l.get('content', '')}" for l in d.get("lists", [])) +
                               f"\n{d.get('additionalPlain', '')}")
        elif m := re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url):
            with urllib.request.urlopen(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}", timeout=20) as r:
                d = json.loads(r.read())
            import html as _h
            text = _guard_text(re.sub(r"<[^>]+>", " ", _h.unescape(d.get("content", ""))))
        else:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "p.html"
                p = subprocess.run([sys.executable, str(ROOT / "tools/fetch_page.py"), url, "--out", str(out)],
                                   capture_output=True, text=True, timeout=90)
                if p.returncode == 0 and out.exists():
                    text = _guard_html_file(out)
    except Exception:  # noqa: BLE001 - any fetch failure means "no text"; callers fall back
        text = None
    if text and len(text) > 200:
        cached.write_text(text, encoding="utf-8")
        return text
    return None
