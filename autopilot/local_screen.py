#!/usr/bin/env python3
"""First-pass screening of newly discovered jobs with the local model (no Claude usage).

Qwen extracts facts; job_assess.decide() applies deterministic rules. Three outcomes:
  relevant    -> Claude /rank prioritises it
  uncertain   -> preserved, Claude reviews it (never dropped)
  irrelevant  -> only with a VERIFIED quote of a hard reason (location restriction, undeclared
                 language, 3+ years / senior title, not a software role). Stored as
                 status "local_rejected" with the evidence - never deleted, and a rotating
                 audit sample stays "new" (local_audit=True) so Claude ranks it and mistakes surface.
Fallback: if the model is disabled, missing, another model is loaded, memory is low, or a call
fails/times out, the job stays "new" and the normal Claude pipeline handles it.
One model at a time (single_instance lock), bounded input, model unloaded at the end.

Usage: local_screen.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_assess  # noqa: E402
import local_llm  # noqa: E402
import posting_text  # noqa: E402
from common import AUTOPILOT, SEEN, config, load_json, log_event, now, save_json, single_instance  # noqa: E402

RUN_LOG = AUTOPILOT / "logs" / "local_screen.jsonl"   # one JSON line per run: decisions, timing, memory, swap


def swap_used_mb() -> float | None:
    import re
    import subprocess
    try:
        o = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"used = ([\d.]+)M", o)
    return float(m.group(1)) if m else None


def write_run_log(rec: dict) -> None:
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    import time
    t_start = time.time()
    st = local_llm.status()
    rec = {"ts": now().isoformat(timespec="seconds"), "model": st.get("model"), "dry_run": a.dry_run,
           "mem_free_pct_before": st.get("mem_free_pct"), "swap_mb_before": swap_used_mb(), "decisions": []}
    if not st["usable"]:
        write_run_log({**rec, "skipped": {k: st.get(k) for k in ("enabled", "installed", "other_model_loaded", "memory_ok", "error")}})
        print(json.dumps({"skipped": st}))
        return 0
    audit_every = int(config().get("local_llm", {}).get("audit_every_nth_rejection", 3))
    with single_instance("local_llm") as ok:
        if not ok:
            return 0
        data = load_json(SEEN, {"seen": {}})
        todo = [(k, e) for k, e in data["seen"].items() if e.get("status") == "new" and "local_outcome" not in e][: a.limit]
        results: dict[str, dict] = {}
        counts = {"relevant": 0, "uncertain": 0, "irrelevant": 0, "fallback": 0}
        n_rej = sum(1 for e in data["seen"].values() if e.get("status") == "local_rejected")
        try:
            for key, e in todo:
                if not local_llm.status()["memory_ok"]:
                    log_event("local_llm_fallback", reason="memory pressure")
                    break
                text = posting_text.fetch(key, e.get("url", ""))
                if not text:
                    counts["fallback"] += 1
                    continue
                res, meta = job_assess.assess(e.get("title", ""), e.get("company", ""), e.get("location", ""), text)
                rec["decisions"].append({"key": key, "seconds": meta.get("seconds"), "mem_free_pct": local_llm.mem_free_pct(),
                                         "outcome": res["outcome"] if res else "fallback",
                                         "error": None if res else str(meta.get("error") or meta.get("skipped"))[:120],
                                         **({"fit": res["assessment"]["fit_score"], "eligibility": res["eligibility"],
                                             "reasons": res["hard_reasons"], "needs_claude_review": res["needs_claude_review"]} if res else {})})
                if res is None:
                    counts["fallback"] += 1
                    log_event("local_llm_fallback", key=key, reason=str(meta.get("error") or meta.get("skipped"))[:120])
                    if meta.get("skipped"):
                        break
                    continue
                asmt = res["assessment"]
                f = {"local_outcome": res["outcome"], "local_score": asmt["fit_score"], "local_eligibility": res["eligibility"],
                     "local_reasons": res["hard_reasons"], "local_needs_review": res["needs_claude_review"],
                     "local_summary": asmt["summary"], "local_category": asmt["category"],
                     "local_facts": {k: asmt[k] for k in ("work_mode", "remote_from_turkey", "years_required", "languages_required",
                                                          "required_skills", "preferred_skills", "matched_skills", "missing_skills")},
                     "local_model": st["model"], "local_date": now().date().isoformat()}
                if res["outcome"] == "irrelevant":
                    rec["decisions"][-1]["audit_sample"] = False
                    n_rej += 1
                    if audit_every and n_rej % audit_every == 0:
                        f["local_audit"] = True        # stays "new": Claude ranks it anyway, so the rejection is checked
                        rec["decisions"][-1]["audit_sample"] = True
                    else:
                        f["status"] = "local_rejected"
                counts[res["outcome"]] += 1
                results[key] = f
        finally:
            local_llm.unload()
            secs = [d["seconds"] for d in rec["decisions"] if d.get("seconds") is not None]
            write_run_log({**rec, "counts": counts, "candidates": len(todo), "total_seconds": round(time.time() - t_start, 1),
                           "median_seconds": sorted(secs)[len(secs) // 2] if secs else None,
                           "max_seconds": max(secs) if secs else None,
                           "mem_free_pct_min": min([d["mem_free_pct"] for d in rec["decisions"] if d.get("mem_free_pct") is not None], default=None),
                           "mem_free_pct_after": local_llm.mem_free_pct(), "swap_mb_after": swap_used_mb()})
        if not a.dry_run and results:
            # Reload and merge only our fields; never overwrite a status someone else changed meanwhile.
            fresh = load_json(SEEN, {"seen": {}})
            for key, fields in results.items():
                cur = fresh["seen"].get(key)
                if not cur:
                    continue
                if cur.get("status") != "new":
                    fields.pop("status", None)
                cur.update(fields)
            save_json(SEEN, fresh)
            log_event("local_screen", model=st["model"], **{k: str(v) for k, v in counts.items()})
        print(json.dumps({"candidates": len(todo), **counts, "dry_run": a.dry_run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
