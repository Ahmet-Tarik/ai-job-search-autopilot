#!/usr/bin/env python3
"""Summarise local-model (Qwen) screening over the last N days. Read-only, no LLM.

Reads logs/local_screen.jsonl (per-run decisions, timing, memory, swap) and logs/local_audit.jsonl
(Claude-vs-Qwen audit of sampled rejections) and prints one JSON summary: postings evaluated,
outcomes, fallbacks/errors, evidence-validation drops, time per posting, memory/swap, model unload,
and audit false negatives. Use it to judge the 9B rollout (see README "Local model").

Usage: local_report.py [--days 7]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import AUTOPILOT, now  # noqa: E402


def lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    since = (now() - dt.timedelta(days=a.days)).isoformat()
    runs = [r for r in lines(AUTOPILOT / "logs" / "local_screen.jsonl") if r.get("ts", "") >= since and not r.get("dry_run")]
    audits = [r for r in lines(AUTOPILOT / "logs" / "local_audit.jsonl") if r.get("ts", "") >= since]
    dec = [d for r in runs for d in r.get("decisions", [])]
    secs = [d["seconds"] for d in dec if d.get("seconds") is not None]
    out = {
        "days": a.days, "runs": len(runs), "runs_skipped": [r["skipped"] for r in runs if r.get("skipped")][-5:],
        "models": sorted({d.get("model") or r.get("model") for r in runs for d in r.get("decisions", [])} - {None}),
        "postings_evaluated": len(dec),
        "outcomes": {o: sum(d.get("outcome") == o for d in dec) for o in ("relevant", "uncertain", "irrelevant", "fallback")},
        "rejections": [{"key": d["key"], "reasons": d.get("reasons"), "audit_sample": d.get("audit_sample")}
                       for d in dec if d.get("outcome") == "irrelevant"],
        "errors": [f"{d['key'][:40]}: {d.get('error')}" for d in dec if d.get("outcome") == "fallback"],
        "evidence_quotes_dropped": sum(d.get("evidence_dropped") or 0 for d in dec),
        "median_seconds": statistics.median(secs) if secs else None, "max_seconds": max(secs) if secs else None,
        "mem_free_pct_min": min([r["mem_free_pct_min"] for r in runs if r.get("mem_free_pct_min") is not None], default=None),
        "swap_mb_max": max([r["swap_mb_max"] for r in runs if r.get("swap_mb_max") is not None], default=None),
        "runs_not_unloaded": sum(1 for r in runs if r.get("decisions") and r.get("model_unloaded") is False),
        "audit_latest": audits[-1] if audits else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
