#!/usr/bin/env python3
"""Benchmark the local model against a reviewed gold set (autopilot/bench_gold.json).

Measures: valid JSON (schema + Pydantic) rate, extraction recall of key terms,
skill precision (extracted required skills that appear in the posting), evidence
quotes dropped (not found in the posting), eligibility accuracy, category accuracy,
FALSE NEGATIVES (suitable job -> irrelevant), false positives (unsuitable -> relevant),
irrelevant recall, score difference from the reviewed (Claude) score, median/worst
time, failures/timeouts, memory pressure and swap. Writes reports/bench_local_<ts>.json.
It never enables the model.

Usage: bench_local.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_assess  # noqa: E402
import local_llm  # noqa: E402
import posting_text  # noqa: E402
from common import AUTOPILOT, REPORTS, now, seen_jobs  # noqa: E402

CLAUDE_TOKENS_PER_JOB = 4200   # measured: posting-scorer batches used ~33-46k tokens per 10 jobs today


def mem_free_pct() -> int | None:
    m = re.search(r"free percentage:\s*(\d+)", subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout)
    return int(m.group(1)) if m else None


def swap_used_mb() -> float | None:
    m = re.search(r"used = ([\d.]+)M", subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout)
    return float(m.group(1)) if m else None


def cat_group(c: str) -> str:
    return {"internship": "intern", "graduate_program": "intern", "new_grad": "intern",
            "junior": "junior", "mid_or_senior": "senior", "not_software": "other"}.get(c, c)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    st = local_llm.status()
    if not st["installed"] or st["other_model_loaded"]:
        print(json.dumps({"cannot_run": st}))
        return 1
    gold_path = AUTOPILOT / "bench_gold.json"   # private: reviewed expectations for your real postings
    if not gold_path.exists():
        print("create autopilot/bench_gold.json (format: bench_gold.example.json) from your own seen jobs")
        return 1
    gold = json.loads(gold_path.read_text(encoding="utf-8"))["items"]
    if a.limit:
        gold = gold[: a.limit]
    seen = seen_jobs()
    rows = []
    swap0, mem0 = swap_used_mb(), mem_free_pct()
    for g in gold:
        e = seen.get(g["key"], {})
        row = {"key": g["key"], "group": g["group"], "claude_score": e.get("rank_score")}
        text = posting_text.fetch(g["key"], e.get("url", ""))
        if not text:
            row["fetch_failed"] = True
            rows.append(row)
            print(f"FETCH FAIL {g['key']}")
            continue
        res, meta = job_assess.assess(e.get("title", ""), e.get("company", ""), e.get("location", ""), text, force=True)
        row.update({"seconds": meta.get("seconds"), "error": meta.get("error"), "ok": res is not None,
                    "mem_free_pct": mem_free_pct(), "prompt_tokens": meta.get("prompt_eval_count")})
        if res:
            asmt = res["assessment"]
            extracted = " | ".join(asmt["required_skills"] + asmt["preferred_skills"]).lower()
            terms = g["key_terms"]
            found = [t for t in terms if t.lower() in extracted]
            row.update({
                "outcome": res["outcome"], "eligibility": res["eligibility"], "qwen_raw_eligibility": asmt["eligibility"],
                "category": asmt["category"], "fit": asmt["fit_score"], "hard_reasons": res["hard_reasons"],
                "needs_review": res["needs_claude_review"], "raw": asmt, "evidence_dropped": res["evidence_dropped"],
                "skills_verified": res["required_skills_verified"], "skills_total": res["required_skills_total"],
                "key_terms_found": len(found), "key_terms_total": len(terms),
                "false_negative": bool(g.get("keep")) and res["outcome"] == "irrelevant",
                "false_positive": bool(g.get("irrelevant")) and res["outcome"] == "relevant",
                "irrelevant_caught": bool(g.get("irrelevant")) and res["outcome"] == "irrelevant",
                "eligibility_correct": res["eligibility"] == g["eligibility"],
                "eligibility_gold": g["eligibility"],
                "category_correct": g["category"] == "any" or cat_group(asmt["category"]) == g["category"],
                "score_diff": abs(asmt["fit_score"] - e["rank_score"]) if e.get("rank_score") is not None else None,
            })
        rows.append(row)
        print(f"{row.get('seconds')}s ok={row['ok']} out={row.get('outcome')} elig={row.get('eligibility')}/{g['eligibility']} "
              f"fit={row.get('fit')} claude={row['claude_score']} | {g['key'][:55]}", flush=True)
    ok = [r for r in rows if r.get("ok")]
    tried = [r for r in rows if not r.get("fetch_failed")]
    keep_n = sum(1 for g in gold if g.get("keep"))
    irr_n = sum(1 for g in gold if g.get("irrelevant"))
    secs = [r["seconds"] for r in ok]
    pct = lambda n, d: round(n / d, 3) if d else None
    s = {
        "model": st["model"], "when": now().isoformat(timespec="minutes"), "postings": len(gold), "fetched": len(tried),
        "valid_json_rate": pct(len(ok), len(tried)),
        "failures_or_timeouts": [r.get("error") for r in tried if not r.get("ok")],
        "key_term_recall": pct(sum(r["key_terms_found"] for r in ok), sum(r["key_terms_total"] for r in ok)),
        "required_skill_precision": pct(sum(r["skills_verified"] for r in ok), sum(r["skills_total"] for r in ok)),
        "evidence_quotes_dropped": sum(r["evidence_dropped"] for r in ok),
        "eligibility_accuracy": pct(sum(r["eligibility_correct"] for r in ok), len(ok)),
        "eligibility_confusions": [f"{r['key'][:40]}: {r['eligibility']} vs gold {r['eligibility_gold']}" for r in ok if not r["eligibility_correct"]],
        "category_accuracy": pct(sum(r["category_correct"] for r in ok), len(ok)),
        "false_negatives": [r["key"] for r in ok if r["false_negative"]],
        "false_negative_rate": pct(sum(r["false_negative"] for r in ok), keep_n),
        "false_positives": [r["key"] for r in ok if r["false_positive"]],
        "false_positive_rate": pct(sum(r["false_positive"] for r in ok), irr_n),
        "irrelevant_recall": pct(sum(r["irrelevant_caught"] for r in ok), irr_n),
        "mean_abs_score_diff_vs_claude": round(statistics.mean([r["score_diff"] for r in ok if r["score_diff"] is not None]), 1) if ok else None,
        "median_seconds": statistics.median(secs) if secs else None, "max_seconds": max(secs) if secs else None,
        "mem_free_pct_before": mem0, "mem_free_pct_min": min([r["mem_free_pct"] for r in ok if r.get("mem_free_pct") is not None], default=None),
        "swap_mb_before": swap0, "swap_mb_after": swap_used_mb(),
        "claude_jobs_avoided": sum(1 for r in ok if r["outcome"] == "irrelevant"),
        "est_claude_tokens_saved_per_100_jobs": round(100 * sum(1 for r in ok if r["outcome"] == "irrelevant") / max(1, len(ok)) * CLAUDE_TOKENS_PER_JOB),
        "needs_claude_review": sum(1 for r in ok if r["needs_review"]),
    }
    s["passed"] = (s["valid_json_rate"] or 0) >= 0.95 and not s["false_negatives"] and (s["eligibility_accuracy"] or 0) >= 0.85 \
        and (s["false_positive_rate"] or 0) <= 0.2 and (s["median_seconds"] or 999) <= 90
    out = REPORTS / f"bench_local_{now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps({"summary": s, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
