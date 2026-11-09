#!/usr/bin/env python3
"""Back up application PDFs to Google Drive via the local Google Drive for desktop folder.

Zero Claude usage: copies cv/*.pdf, cover_letters/*.pdf and each packet's
form_plan.md into "My Drive/Job Search Autopilot/Applications/<slug>/" (a private
folder - nothing is shared). Only new or changed files are copied. If the Drive
app is not running the folder hangs or is missing: every filesystem call runs
with a timeout, the run is skipped and logged, and it retries next time.

Usage: drive_backup.py [--dry-run]
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import APPS, ROOT, STATE, load_json, log_event, save_json, single_instance  # noqa: E402

DRIVE = Path.home() / "Library/CloudStorage/GoogleDrive-you@example.com/My Drive/Job Search Autopilot/Applications"
MANIFEST = STATE / "drive_backup.json"


def run(cmd: list[str], timeout: int = 30) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def files_to_backup() -> list[tuple[str, Path]]:
    out = []
    for packet in APPS.glob("*/packet.json"):
        slug = packet.parent.name
        for f in (ROOT / "cv" / f"main_{slug}.pdf", ROOT / "cover_letters" / f"cover_{slug}.pdf",
                  packet.parent / "form_plan.md"):
            if f.exists():
                out.append((slug, f))
    # applications made outside the packet flow (e.g. the first manual one)
    for pdf in (ROOT / "cv").glob("main_*.pdf"):
        slug = pdf.stem[len("main_"):]
        if slug != "example" and not any(s == slug for s, _ in out):
            out.append((slug, pdf))
            cover = ROOT / "cover_letters" / f"cover_{slug}.pdf"
            if cover.exists():
                out.append((slug, cover))
    return out


def main() -> int:
    dry = "--dry-run" in sys.argv
    with single_instance("drive") as ok:
        if not ok:
            return 0
        if not run(["test", "-d", str(DRIVE.parent.parent)], timeout=15):
            log_event("drive_backup_skipped", reason="Google Drive app not running or folder unreachable")
            print("skipped: Drive folder unreachable (is Google Drive for desktop running?)")
            return 0
        manifest = load_json(MANIFEST, {})
        copied = 0
        for slug, f in files_to_backup():
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            key = f"{slug}/{f.name}"
            if manifest.get(key) == digest:
                continue
            dest = DRIVE / slug
            if dry:
                print("would copy", key)
                continue
            if run(["mkdir", "-p", str(dest)]) and run(["cp", str(f), str(dest / f.name)], timeout=60):
                manifest[key] = digest
                copied += 1
            else:
                log_event("drive_backup_skipped", reason=f"copy timed out for {key}")
                break
        if not dry:
            save_json(MANIFEST, manifest)
            log_event("drive_backup", copied=copied)
        print(f"drive backup: {copied} file(s) copied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
