#!/usr/bin/env python3
"""Snapshot backup of the job-search project to the candidate's own Google Drive (Drive for desktop).

Each run that finds changes writes a NEW timestamped snapshot folder; nothing in Drive is ever
overwritten or deleted (no pruning):
  My Drive/Job Search Autopilot/Backups/<YYYY-MM-DD_HHMMSS>/
    job-search.bundle    git bundle of ALL branches and commits (code, config, docs, profile, CV
                         templates, tests) - restore with `git clone job-search.bundle job-search`
    worktree.patch       uncommitted changes to tracked files (only when there are any)
    data.tar.gz          git-ignored personal data: tracker, seen jobs, applications/packets,
                         tailored CVs and cover letters (.tex + .pdf), company research, LinkedIn
                         PDF, autopilot state/logs/reports, local Claude settings
    MANIFEST.json        every file in data.tar.gz with size + sha256, the bundle's HEADs, the
                         archive checksums, and the exclusions
    RESTORE.md           how to restore
The folder is written as "<ts>.partial" and renamed only after every file was copied and its
checksum re-read from Drive matched, so a half-written snapshot never looks complete.

Never included: credentials (~/.config/job-search/telegram.env lives outside the project anyway),
autopilot/form_answers.json (sensitive personal answers - needs the candidate's approval),
autopilot/state/email_batch.json (snippets of real emails; transient), lock files, caches,
node_modules, model weights, .git internals (the bundle replaces them).

Failure handling: if Drive is not running, syncing is paused, the folder is unreadable, a copy
fails or a checksum differs, the run logs "drive_backup_skipped" with the reason (shown in the
evening briefing), sends a notification, and exits with status 1. It never reports success
without a verified snapshot. If nothing changed since the last verified snapshot, no new folder
is created ("unchanged").

Usage: drive_backup.py [--dry-run] [--force] [--status]
  --dry-run  list what would be backed up, touch nothing
  --force    write a snapshot even if nothing changed
  --status   print Drive availability and the last snapshot, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, STATE, config, load_json, log_event, notify, now, save_json, single_instance  # noqa: E402

_BK = config().get("backup", {})
ACCOUNT_ROOT = Path(_BK.get("drive_account_root", "~/Library/CloudStorage/GoogleDrive-you@example.com")).expanduser()
MY_DRIVE = ACCOUNT_ROOT / "My Drive"
DEST = MY_DRIVE / _BK.get("destination", "Job Search Autopilot/Backups")
DRIVE_LOG = Path.home() / "Library/Application Support/Google/DriveFS/Logs/drive_fs.txt"
LAST = STATE / "drive_backup.json"

# git-ignored, non-reproducible data (paths relative to the project root)
DATA_INCLUDE = [
    "job_search_tracker.csv", "job_scraper", "documents", "cv", "cover_letters", "company_research",
    "upskill", "autopilot/state", "autopilot/logs", "autopilot/reports", ".claude/settings.local.json",
]
EXCLUDE = re.compile(
    r"(^|/)(node_modules|__pycache__|\.pytest_cache|\.git)(/|$)|(^|/)\.DS_Store$|\.lock$|\.partial$|"
    r"^autopilot/form_answers\.json$|^autopilot/state/email_batch\.json$|"
    r"(^|/)[^/]*\.(gguf|safetensors|bin)$|(^|/)\.env[^/]*$|telegram\.env$|token[^/]*\.json$|credentials[^/]*\.json$|"
    r"^(cv|cover_letters)/.*\.(aux|log|out|fls|fdb_latexmk|synctex\.gz|xdv)$")
SENSITIVE_EXCLUDED = ["autopilot/form_answers.json (personal form answers; needs approval)",
                      "autopilot/state/email_batch.json (email snippets; transient)",
                      "~/.config/job-search/telegram.env (bot token; outside the project)"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None


def drive_status() -> tuple[bool, str]:
    """(usable, reason). Checks the app, the pause state and real readability (a stale mount passes `test -d`)."""
    p = run(["pgrep", "-f", "Google Drive.app/Contents/MacOS/Google Drive"], timeout=10)
    if not p or p.returncode != 0:
        return False, "Google Drive for desktop is not running"
    try:   # the newest pause/resume line in Drive's own log
        tail = DRIVE_LOG.read_bytes()[-400_000:].decode("utf-8", "ignore")
        marks = re.findall(r"Syncing is (paused|on|resumed)", tail)
        if marks and marks[-1] == "paused":
            return False, "Google Drive syncing is paused (Drive menu-bar icon > gear > Resume syncing)"
    except OSError:
        pass
    ls = run(["ls", str(MY_DRIVE)], timeout=20)
    if ls is None:
        return False, "My Drive is mounted but not readable (timed out) - Drive disconnected or paused"
    if ls.returncode != 0:
        return False, f"My Drive not readable: {ls.stderr.decode(errors='ignore')[:120]}"
    return True, "ok"


def collect() -> list[str]:
    files = []
    for inc in DATA_INCLUDE:
        p = ROOT / inc
        if p.is_file():
            cands = [p]
        elif p.is_dir():
            cands = [x for x in p.rglob("*") if x.is_file()]
        else:
            continue
        for f in cands:
            rel = f.relative_to(ROOT).as_posix()
            if not EXCLUDE.search(rel) and not f.is_symlink():
                files.append(rel)
    return sorted(set(files))


def build_snapshot(work: Path) -> dict:
    """Write bundle, patch, data archive and manifest into `work`. Returns the manifest."""
    b = run(["git", "bundle", "create", str(work / "job-search.bundle"), "--all"], timeout=120, cwd=ROOT)
    if not b or b.returncode != 0:
        raise RuntimeError(f"git bundle failed: {(b.stderr.decode(errors='ignore') if b else 'timeout')[:200]}")
    v = run(["git", "bundle", "verify", str(work / "job-search.bundle")], timeout=60, cwd=ROOT)
    if not v or v.returncode != 0:
        raise RuntimeError("git bundle verify failed")
    heads = run(["git", "bundle", "list-heads", str(work / "job-search.bundle")], timeout=30, cwd=ROOT).stdout.decode()
    diff = run(["git", "diff", "--binary", "HEAD"], timeout=60, cwd=ROOT).stdout
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], timeout=60, cwd=ROOT).stdout.decode().split()
    untracked = [u for u in untracked if not EXCLUDE.search(u)]
    if diff:
        (work / "worktree.patch").write_bytes(diff)
    files = collect() + untracked
    entries = []
    with tarfile.open(work / "data.tar.gz", "w:gz") as tar:
        for rel in sorted(set(files)):
            src = ROOT / rel
            try:
                entries.append({"path": rel, "size": src.stat().st_size, "sha256": sha256(src)})
                tar.add(src, arcname=rel, recursive=False)
            except OSError as exc:
                raise RuntimeError(f"cannot read {rel}: {exc}") from exc
    content_fp = hashlib.sha256(json.dumps([(e["path"], e["sha256"]) for e in entries]).encode() + heads.encode()
                                + hashlib.sha256(diff).hexdigest().encode()).hexdigest()
    manifest = {
        "created": now().isoformat(timespec="seconds"), "project_root": str(ROOT), "content_fingerprint": content_fp,
        "git_heads": heads.strip().splitlines(), "worktree_patch": bool(diff), "untracked_in_archive": untracked,
        "files": entries, "file_count": len(entries), "bytes": sum(e["size"] for e in entries),
        "excluded_sensitive": SENSITIVE_EXCLUDED,
        "artifacts": {},
    }
    (work / "RESTORE.md").write_text(RESTORE_MD, encoding="utf-8")
    for name in ("job-search.bundle", "data.tar.gz", "worktree.patch", "RESTORE.md"):
        if (work / name).exists():
            manifest["artifacts"][name] = {"size": (work / name).stat().st_size, "sha256": sha256(work / name)}
    (work / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


RESTORE_MD = """# Restoring the job-search project from this snapshot

1. Copy this snapshot folder from Google Drive to the Mac (or download it from drive.google.com).
2. Code, config, docs and the full git history:
       git clone job-search.bundle ~/Private/job-search
       cd ~/Private/job-search && git checkout profile-setup     # the working branch
   If worktree.patch exists:  git apply /path/to/snapshot/worktree.patch
3. Personal data (tracker, applications, CVs/cover letters, state, logs, reports):
       tar -xzf /path/to/snapshot/data.tar.gz -C ~/Private/job-search
4. Verify:  python3 autopilot/drive_backup.py --verify-dir /path/to/snapshot   (checks every file against MANIFEST.json)
5. Recreate what is deliberately NOT in the backup:
   - ~/.config/job-search/telegram.env (bot token; re-run autopilot/telegram_setup.py)
   - autopilot/form_answers.json (recreate from the candidate profile)
   - `bun install` inside each .agents/skills/*/cli for node_modules; Ollama models via `ollama pull`
   - launchd jobs: autopilot/launchd/install.sh; Claude scheduled tasks via the Claude app
"""


def verify_dir(snap: Path, extract_to: Path | None = None) -> tuple[bool, str]:
    """Re-read every artifact from `snap`, compare checksums; optionally extract data and compare every file."""
    try:
        m = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"manifest unreadable: {exc}"
    for name, meta in m["artifacts"].items():
        p = snap / name
        if not p.exists() or p.stat().st_size != meta["size"] or sha256(p) != meta["sha256"]:
            return False, f"{name} missing or checksum mismatch"
    if extract_to is not None:
        with tarfile.open(snap / "data.tar.gz", "r:gz") as tar:
            tar.extractall(extract_to, filter="data")
        for e in m["files"]:
            f = extract_to / e["path"]
            if not f.exists() or f.stat().st_size != e["size"] or sha256(f) != e["sha256"]:
                return False, f"restored file differs: {e['path']}"
        clone = extract_to / "_repo"
        c = run(["git", "clone", "-q", str(snap / "job-search.bundle"), str(clone)], timeout=120)
        if not c or c.returncode != 0:
            return False, "git clone from bundle failed"
        head = run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.decode().strip()
        if not any(line.startswith(head) for line in m["git_heads"]):
            return False, "cloned HEAD not among bundled heads"
    return True, f"{m['file_count']} files, {len(m['artifacts'])} artifacts verified"


def fail(reason: str) -> int:
    log_event("drive_backup_skipped", reason=reason[:200])
    try:
        notify("Job search backup FAILED", reason)
    except Exception:  # noqa: BLE001
        pass
    print(f"BACKUP FAILED: {reason}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--verify-dir")
    a = ap.parse_args()
    if a.verify_dir:
        with tempfile.TemporaryDirectory() as t:
            ok, msg = verify_dir(Path(a.verify_dir), Path(t))
        print(("OK " if ok else "FAILED ") + msg)
        return 0 if ok else 1
    usable, reason = drive_status()
    last = load_json(LAST, {})
    if a.status:
        print(json.dumps({"drive_usable": usable, "reason": reason, "destination": str(DEST), "last": last}, indent=1))
        return 0 if usable else 1
    if a.dry_run:
        files = collect()
        total = sum((ROOT / f).stat().st_size for f in files)
        print(json.dumps({"drive_usable": usable, "reason": reason, "destination": str(DEST),
                          "data_files": len(files), "data_bytes": total, "files": files,
                          "plus": ["job-search.bundle (git bundle --all)", "worktree.patch if uncommitted changes",
                                   "MANIFEST.json", "RESTORE.md"], "excluded_sensitive": SENSITIVE_EXCLUDED}, indent=1))
        return 0
    with single_instance("drive") as ok:
        if not ok:
            return fail("another backup is still running")
        if not usable:
            return fail(reason)
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="jsbackup_") as tmp:
            work = Path(tmp)
            try:
                manifest = build_snapshot(work)
            except RuntimeError as exc:
                return fail(str(exc))
            if not a.force and manifest["content_fingerprint"] == last.get("content_fingerprint"):
                log_event("drive_backup", copied="0", result="unchanged", snapshot=last.get("snapshot", ""))
                print(f"unchanged since snapshot {last.get('snapshot')} - nothing new written")
                return 0
            ts = now().strftime("%Y-%m-%d_%H%M%S")
            partial, final = DEST / f"{ts}.partial", DEST / ts
            if final.exists() or partial.exists():
                return fail(f"snapshot folder {ts} already exists - refusing to overwrite")
            mk = run(["mkdir", "-p", str(partial)], timeout=60)
            if not mk or mk.returncode != 0:
                return fail("cannot create the snapshot folder in Drive")
            for f in sorted(work.iterdir()):
                cp = run(["cp", str(f), str(partial / f.name)], timeout=600)
                if not cp or cp.returncode != 0:
                    return fail(f"copy to Drive failed for {f.name} (left as {partial.name}, not deleted)")
            ok_v, msg = verify_dir(partial)
            if not ok_v:
                return fail(f"verification in Drive failed: {msg} (left as {partial.name})")
            try:
                os.rename(partial, final)
            except OSError as exc:
                return fail(f"could not finalise snapshot: {exc}")
        rec = {"snapshot": ts, "path": str(final), "content_fingerprint": manifest["content_fingerprint"],
               "file_count": manifest["file_count"], "bytes": manifest["bytes"],
               "artifacts": {k: v["size"] for k, v in manifest["artifacts"].items()},
               "finished": now().isoformat(timespec="seconds"), "seconds": round(time.time() - t0, 1)}
        save_json(LAST, rec)
        log_event("drive_backup", copied=str(manifest["file_count"]), result="snapshot", snapshot=ts)
        print(json.dumps(rec, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
