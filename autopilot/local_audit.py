#!/usr/bin/env python3
"""Compare local-model (Qwen) screening with Claude's ranking. No LLM.

- Audit-sampled rejections (local_audit=True) stay in the Claude queue; once Claude ranked one,
  a Claude score >= the category threshold with all gates passing is a LOCAL FALSE NEGATIVE.
- Also reports disagreement on 'relevant' vs Claude.
Prints JSON; logs a local_false_negative event per miss. With --auto-disable, two or more
false negatives in the last 14 days set local_llm.enabled=false (Claude-only fallback).

Usage: local_audit.py [--auto-disable]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import AUTOPILOT, config, log_event, now, read_events, seen_jobs  # noqa: E402

THRESH = {"internship": 50, "graduate_program": 50, "new_grad": 50}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto-disable", action="store_true")
    a = ap.parse_args()
    logged = {e.get("key") for e in read_events() if e["type"] == "local_false_negative"}
    out = {"audited": 0, "false_negatives": [], "agree_relevant": 0, "disagree_relevant": 0, "pending_audit": 0}
    for key, e in seen_jobs().items():
        if "local_outcome" not in e:
            continue
        if e.get("status") != "ranked":
            if e.get("local_audit"):
                out["pending_audit"] += 1
            continue
        thr = THRESH.get(e.get("local_category"), 60)
        claude_ok = (e.get("rank_score") or 0) >= thr and e.get("location_verdict") != "FAIL" and e.get("language_gate") != "FAIL"
        if e["local_outcome"] == "irrelevant":
            out["audited"] += 1
            if claude_ok:
                out["false_negatives"].append({"key": key, "claude": e.get("rank_score"), "local_reasons": e.get("local_reasons")})
                if key not in logged:
                    log_event("local_false_negative", key=key, claude=e.get("rank_score"))
        elif e["local_outcome"] == "relevant":
            out["agree_relevant" if claude_ok else "disagree_relevant"] += 1
    if a.auto_disable:
        cutoff = (now() - __import__("datetime").timedelta(days=14)).isoformat()
        recent = [e for e in read_events() if e["type"] == "local_false_negative" and e["ts"] >= cutoff]
        if len(recent) >= 2 and config().get("local_llm", {}).get("enabled"):
            p = AUTOPILOT / "config.json"
            c = json.loads(p.read_text(encoding="utf-8"))
            c["local_llm"]["enabled"] = False
            p.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log_event("local_llm_disabled", reason=f"{len(recent)} false negatives in 14 days")
            out["auto_disabled"] = True
    with (AUTOPILOT / "logs" / "local_audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now().isoformat(timespec="seconds"), **out}, ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
