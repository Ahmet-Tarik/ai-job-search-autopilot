#!/usr/bin/env python3
"""Daily numbered shortlist: hard eligibility gates first, soft fit second.

Two pipelines with their own fit thresholds (config.json "shortlist"):
  internship  (internships, off-cycle, summer 2027, working student, graduate programs, new grad) >= 50
  junior      (junior / entry-level / 0-2 years software roles)                                  >= 60

Hard gates (never shortlisted): location FAIL (e.g. remote restricted to other countries,
"no sponsorship" abroad), language FAIL (undeclared language required), excluded by the
candidate (do_not_draft), already applied, closed/expired.
Uncertain (shortlisted, flagged): sponsorship not stated, Istanbul onsite (relocation),
last-resort locations (work right unverified), language bar above B2, preliminary score only.

Numbers are stable for the day: "apply to 1, 3" always refers to state/shortlist_<date>.json.

Usage:
  shortlist.py build [--send] [--dry-run]   build today's list (and send it to Telegram)
  shortlist.py show [--date YYYY-MM-DD]     print a saved list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import STATE, config, excluded_company, load_json, log_event, now, notify, save_json, seen_jobs, send_telegram_ids, tracker_rows  # noqa: E402

TR = re.compile(r"t[üu]rk(iye|ey)|istanbul|İstanbul|ankara|izmir|kocaeli|bursa|antalya|\btr\b", re.I)
IST = re.compile(r"istanbul|İstanbul", re.I)
ANK = re.compile(r"ankara", re.I)
AZ = re.compile(r"(?!x)x", re.I)
REMOTE = re.compile(r"remote|uzaktan|worldwide|anywhere|emea", re.I)


def category(title: str, cfg: dict) -> str:
    if re.search(cfg["internship_regex"], title, re.I):
        return "internship"
    return "junior"


def location_info(e: dict) -> tuple[int, str, str | None]:
    """(priority, remote_eligibility text, uncertainty flag or None). Lower priority is better."""
    loc = f"{e.get('location') or ''} {e.get('title') or ''}"
    verdict = e.get("location_verdict")
    multi_country = (e.get("location") or "").count(",") >= 3
    if multi_country:
        if re.search(r"turkey|türkiye", loc, re.I):
            return 3, "remote, multi-country list includes Turkey", None
        return 5, "remote, country list (Turkey not listed)", "Turkey not in the allowed countries"
    if AZ.search(loc):
        return 6, "last-resort location", "work right unverified (last resort)"
    if ANK.search(loc):
        return 1, ("Ankara, remote possible" if REMOTE.search(loc) else "Ankara onsite/hybrid"), None
    if TR.search(loc) and REMOTE.search(loc):
        return 2, "remote, Turkey", None
    if IST.search(loc):
        if REMOTE.search(loc):
            return 2, "remote (Istanbul company)", None
        return 4, "Istanbul onsite/hybrid", "relocation from Ankara"
    if TR.search(loc):
        if verdict == "PASS":
            return 2, "Turkey - eligible (confirmed in review)", None
        return 3, "Turkey, outside Ankara", "commute/relocation from Ankara or remote not confirmed"
    if REMOTE.search(loc):
        if verdict == "PASS":
            return 3, "remote, open to Turkey", None
        return 3, "remote (eligibility unclear)", "confirm candidates in Turkey are accepted"
    if verdict == "PASS":
        return 5, "onsite abroad, sponsorship stated", None
    return 5, "onsite abroad", "visa sponsorship not stated"


def build(dry_run: bool = False) -> dict:
    cfg_all = config()
    cfg = cfg_all["shortlist"]
    excluded = cfg_all["drafting"].get("do_not_draft", {})
    applied = [(r.get("company", "").lower(), r.get("role", "").lower()) for r in tracker_rows()]
    items, rejected = [], {}
    for key, e in seen_jobs().items():
        status = e.get("status")
        score = e.get("rank_score")
        prelim = False
        if status != "ranked":
            if status == "new" and e.get("local_score") is not None:   # local first-pass score (Qwen), if enabled
                score, prelim = e["local_score"], True
            else:
                continue
        title, company = e.get("title", ""), e.get("company", "")
        # ---- hard gates -------------------------------------------------------------
        reason = None
        if e.get("location_verdict") == "FAIL":
            reason = "location not eligible"
        elif e.get("language_gate") == "FAIL":
            reason = "requires an undeclared language"
        elif key in excluded or excluded_company(company):
            reason = "excluded by you"
        elif any(company.lower() == c and r and r in title.lower() for c, r in applied):
            reason = "already applied"
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        # ---- soft fit ---------------------------------------------------------------
        cat = category(title, cfg)
        threshold = cfg["threshold_internship"] if cat == "internship" else cfg["threshold_junior"]
        if score is None or score < threshold:
            continue
        prio, remote_txt, loc_flag = location_info(e)
        risks = [f for f in (loc_flag,) if f]
        if e.get("language_gate") == "FLAG":
            risks.append(e.get("language_note") or "language bar may exceed B2")
        if prelim:
            risks.append("preliminary local score - Claude review pending")
        risks += [g for g in (e.get("gaps") or [])[:1]]
        items.append({"key": key, "company": company, "title": title, "location": (e.get("location") or "")[:60],
                      "category": cat, "score": score, "prelim": prelim, "priority": prio,
                      "remote": remote_txt, "risks": risks, "deadline": e.get("deadline"),
                      "url": e.get("url"), "eligibility": "uncertain" if loc_flag or e.get("language_gate") == "FLAG" else "eligible"})
    items.sort(key=lambda r: (r["priority"], r["category"] != "internship", -r["score"]))
    items = items[: cfg["max_items"]]
    import posting_status  # direct employer routes for LinkedIn items (cached per day)
    for it in items:
        if "linkedin.com" in (it["url"] or ""):
            try:
                d = posting_status.find_direct_route(it["company"], it["title"], it.get("location") or "")
            except Exception:  # noqa: BLE001
                d = None
            it["route"] = f"direct {d['ats']} form (confirmed open)" if d else "LinkedIn (verified before submission)"
            if d:
                it["apply_url"] = d["apply_url"]
        elif re.search(r"lever\.co|greenhouse\.io", it["url"] or ""):
            it["route"] = "direct ATS form"
        else:
            it["route"] = "company/portal page (verified before submission)"
    for i, it in enumerate(items, 1):
        it["n"] = i
    result = {"date": now().date().isoformat(), "built": now().isoformat(timespec="seconds"),
              "items": items, "hard_gate_rejections": rejected}
    if not dry_run:
        save_json(STATE / "shortlist_draft.json", result)   # working copy only; never used for replies
        log_event("shortlist_built", items=len(items))
    return result


PUBLISHED_DIR = STATE / "shortlists"
INDEX = STATE / "shortlist_index.json"


def publish(sl: dict, message_ids: list[int] | None = None) -> str:
    """Freeze a list under a new immutable id. Replies always resolve against a frozen list."""
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    idx = load_json(INDEX, {"lists": [], "by_message_id": {}})
    seq = 1 + sum(1 for x in idx["lists"] if x.startswith(sl["date"]))
    list_id = f"{sl['date']}-{seq}"
    path = PUBLISHED_DIR / f"{list_id}.json"
    if path.exists():
        raise RuntimeError(f"refusing to overwrite published list {list_id}")
    frozen = {**sl, "list_id": list_id, "published": now().isoformat(timespec="seconds")}
    save_json(path, frozen)
    path.chmod(0o444)                      # read-only on disk as an extra guard
    idx["lists"].append(list_id)
    idx["latest"] = list_id
    for mid in message_ids or []:
        idx["by_message_id"][str(mid)] = list_id
    save_json(INDEX, idx)
    return list_id


def load_published(list_id: str | None = None, message_id: int | None = None) -> dict | None:
    idx = load_json(INDEX, {"lists": [], "by_message_id": {}})
    if message_id is not None and str(message_id) in idx["by_message_id"]:
        list_id = idx["by_message_id"][str(message_id)]
    list_id = list_id or idx.get("latest")
    return load_json(PUBLISHED_DIR / f"{list_id}.json", None) if list_id else None


def bind_messages(list_id: str, message_ids: list[int]) -> None:
    idx = load_json(INDEX, {"lists": [], "by_message_id": {}})
    for mid in message_ids:
        idx["by_message_id"][str(mid)] = list_id
    save_json(INDEX, idx)


def render(sl: dict) -> str:
    lid = sl.get("list_id", "draft")
    lines = [f"Job shortlist {lid} - reply e.g. 'apply to 1, 3' (numbers refer to THIS list only)", ""]
    for it in sl["items"]:
        head = f"{it['n']}. [{'INTERN/NEW-GRAD' if it['category'] == 'internship' else 'JUNIOR'}] {it['title']} - {it['company']}"
        fit = f"fit {it['score']}{' (prelim)' if it['prelim'] else ''}"
        lines.append(head)
        lines.append(f"   {it['location'] or '?'} | {it['remote']} | {fit} | {it['eligibility']}"
                     + (f" | deadline {it['deadline']}" if it.get("deadline") else ""))
        if it["risks"]:
            lines.append("   risks: " + "; ".join(r[:110] for r in it["risks"][:2]))
        lines.append(f"   route: {it.get('route', '?')}")
        lines.append(f"   {it.get('apply_url') or it['url']}")
    if not sl["items"]:
        lines.append("No new opportunities passed the eligibility gates and fit thresholds today.")
    rej = sl.get("hard_gate_rejections") or {}
    if rej:
        lines.append("")
        lines.append("Filtered by hard eligibility: " + ", ".join(f"{v} {k}" for k, v in rej.items()))
    lines.append("")
    lines.append("Replying 'apply to N' authorizes preparing tailored documents and the form plan. "
                 "Submission is confirmed by you in Claude ('submit packets ...').")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--send", action="store_true")
    b.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("--date", help="published list id, e.g. 2026-11-09-1")
    a = ap.parse_args()
    if a.cmd == "build":
        sl = build(dry_run=a.dry_run)
        text = render(sl)
        print(text)
        if a.send and not a.dry_run:
            sent_key = f"shortlist-{sl['date']}"
            state = load_json(STATE / "briefing_state.json", {})
            if sent_key in state.get("sent", []):
                print("(already sent today)")
                return 0
            list_id = publish(sl)                       # freeze first, then send the frozen text
            frozen = load_published(list_id)
            ids, reason = send_telegram_ids(render(frozen))
            bind_messages(list_id, ids)
            channel = "telegram" if ids else notify("Job shortlist " + list_id, "Telegram failed: " + reason)
            state.setdefault("sent", []).append(sent_key)
            save_json(STATE / "briefing_state.json", state)
            log_event("shortlist_sent", items=len(sl["items"]), channel=channel, list_id=list_id)
        return 0
    sl = load_published(a.date) if a.date else load_published()
    print(render(sl) if sl else "no published shortlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
