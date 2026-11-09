#!/usr/bin/env python3
"""Is a posting still open, and is there a usable application route? Run BEFORE any drafting.

Checks (no LLM, no login):
  Lever       public API: 404 -> closed; open -> route "ats_form" (jobs.lever.co .../apply)
  Greenhouse  public API: 404 -> closed; open -> route "ats_form"
  LinkedIn    portal CLI detail: closed banner -> closed; else "open (signed-out view)".
              LinkedIn hides some closures ("Not currently accepting applications") from
              signed-out visitors, so the result is marked needs_browser_check=True: the
              Claude run must confirm in the signed-in built-in browser before drafting.
  Other       tools/fetch_page.py (robots.txt enforced) and a scan for closed phrases.

States: open (confirmed, e.g. live ATS posting), closed (confirmed), unknown (needs verification,
e.g. LinkedIn signed-out view). Exit codes: 0 open, 3 closed, 4 unknown. Prints JSON incl. "state".
For LinkedIn jobs it first looks for the same job on the employer's own Lever/Greenhouse board
(public APIs); a match gives a confirmed-open direct application route.
With --record KEY, a closed result marks the job "expired" in seen_jobs.json (reason kept)
and logs a "posting_closed" event, so it never reaches the shortlist or drafting again.

Usage: posting_status.py URL [--record SEEN_KEY] [--mark-closed "reason seen in browser"]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, SEEN, load_json, log_event, now, save_json  # noqa: E402

CLOSED = re.compile(
    r"no longer accepting applications|not currently accepting applications|this job is no longer available|"
    r"position has been filled|job (posting )?(has )?expired|applications? (are |is )?closed|"
    r"artık başvuru kabul (etmiyor|edilmiyor)|başvurular(a)? kapan(dı|mıştır)|ilan (yayından kaldırıldı|süresi doldu)", re.I)


def _status_code(url: str) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError):
        return 0


def _slugs(company: str) -> list[str]:
    import unicodedata
    base = unicodedata.normalize("NFKD", company).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"\b(a\.?s\.?|inc|ltd|llc|gmbh|teknoloji|technology|technologies|software|yazilim|bilisim|group|grubu)\b\.?", " ", base)
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    known = []
    try:  # board slugs already verified in config (e.g. Peak -> peakgames)
        import ats_sources
        known = [slug for slug, name in ats_sources.DISPLAY.items() if name.lower() == company.lower().strip()]
    except Exception:  # noqa: BLE001
        pass
    cands = known + ["".join(words), "-".join(words), words[0]]
    cands += ["".join(words) + "games", "".join(words) + "tech", words[0] + "-tech", words[0] + "hq"]
    seen, out = set(), []
    for c in cands:
        if len(c) >= 3 and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:8]


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()


IGNORABLE_TITLE = {"m", "f", "d", "w", "x", "mwd", "mfd", "all", "genders", "remote", "hybrid", "onsite", "on", "site",
                   "the", "a", "an", "of", "for", "and", "in", "at", "to", "uzaktan", "hibrit"}
PLACE_ALIASES = {"turkiye": "turkey", "türkiye": "turkey", "tr": "turkey", "istanbul": "istanbul", "i̇stanbul": "istanbul"}
TR_CITIES = {"istanbul", "ankara", "izmir", "kocaeli", "bursa", "antalya", "gebze", "konya", "eskisehir"}
REQ_ID = re.compile(r"\b(?:req(?:uisition)?|job|posting|ref(?:erence)?)\s*(?:id|no|number|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9_-]{3,})", re.I)


def _title_tokens(t: str) -> frozenset[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = t.replace("c#", "csharp").replace(".net", "dotnet").replace("node.js", "nodejs").replace("full-stack", "fullstack") \
         .replace("full stack", "fullstack").replace("back-end", "backend").replace("front-end", "frontend")
    toks = set(re.findall(r"[a-z0-9]+", t)) - IGNORABLE_TITLE
    return frozenset(toks - TR_CITIES - {"turkey", "turkiye"})


def _places(loc: str) -> set[str]:
    import unicodedata
    l = unicodedata.normalize("NFKD", loc or "").encode("ascii", "ignore").decode().lower()
    words = set(re.findall(r"[a-z]+", l))
    out = {PLACE_ALIASES.get(w, w) for w in words}
    if out & TR_CITIES:
        out.add("turkey")
    # not places: some boards put contract types in the location field (Peak: "Full-time")
    return out - {"remote", "hybrid", "on", "site", "onsite", "office", "area", "metropolitan", "province", "region",
                  "full", "time", "part", "contract", "permanent", "temporary", "internship", "intern", "new", "grad",
                  "freelance", "multiple", "locations", "various", "anywhere", "worldwide", "global"}


def _mode(*texts: str) -> str | None:
    t = " ".join(x or "" for x in texts).lower()
    if re.search(r"\bhybrid\b|hibrit", t):
        return "hybrid"
    if re.search(r"\bremote\b|uzaktan", t):
        return "remote"
    if re.search(r"on-?site|ofisten|in office", t):
        return "onsite"
    return None


def _identity(company: str, ats: str, slug: str, board_name: str | None, known: set[str]) -> str | None:
    """Return how the employer identity was confirmed, or None if it is not."""
    from common import _company_norm
    cn = _company_norm(company).replace(" ", "")
    if slug in known:
        return f"verified slug '{slug}' for {company}"
    if board_name and _company_norm(board_name).replace(" ", "") == cn:
        return f"board name '{board_name}' equals company"
    if ats == "lever" and slug.replace("-", "") == cn and len(cn) >= 5:
        return f"lever slug '{slug}' is the full company name"
    return None


def _fetch_boards(company: str) -> tuple[list[dict], bool]:
    known = set()
    try:
        import ats_sources
        known = {slug for slug, name in ats_sources.DISPLAY.items() if name.lower() == company.lower().strip()}
    except Exception:  # noqa: BLE001
        pass
    boards, uncertain = [], False
    for slug in _slugs(company):
        for ats, api in (("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json"),
                         ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")):
            try:
                with urllib.request.urlopen(api, timeout=20) as r:
                    data = json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    uncertain = True
                continue
            except Exception:  # noqa: BLE001 - network trouble: do not cache a negative answer
                uncertain = True
                continue
            jobs = data if isinstance(data, list) else data.get("jobs", [])
            if not jobs:
                continue
            board_name = None
            if ats == "greenhouse":
                try:
                    with urllib.request.urlopen(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout=20) as r:
                        board_name = json.loads(r.read()).get("name")
                except Exception:  # noqa: BLE001
                    uncertain = True
            rows = []
            for j in jobs:
                cats = j.get("categories") or {}
                loc = cats.get("location") or (j.get("location") or {}).get("name") or ""
                if cats.get("allLocations"):
                    loc = "; ".join(cats["allLocations"])
                rows.append({"id": str(j.get("id") or ""), "req": str(j.get("requisition_id") or ""),
                             "title": j.get("text") or j.get("title"), "url": j.get("hostedUrl") or j.get("absolute_url"),
                             "location": loc, "workplace": (j.get("workplaceType") or "").lower()})
            boards.append({"ats": ats, "slug": slug, "board_name": board_name,
                           "identity": _identity(company, ats, slug, board_name, known), "jobs": rows})
    return boards, uncertain


def match_direct_route(company: str, title: str, location: str = "", text: str = "", boards: list[dict] | None = None) -> dict:
    """Strict same-job match on the employer's own Lever/Greenhouse board.

    Accept only when ALL hold: employer identity confirmed (known slug, Greenhouse board name, or a Lever
    slug equal to the full company name); core title tokens IDENTICAL (no 'similar' titles); location
    compatible (shared country/city when both are stated); work mode not contradictory; requisition id
    equal when both sides state one; and exactly ONE posting remains. Otherwise {"match": False, "reason"}.
    """
    from common import STATE
    if boards is None:
        cache_path = STATE / "ats_routes.json"
        cache = load_json(cache_path, {})
        key = "v2:" + company.lower()
        today = now().date().isoformat()
        entry = cache.get(key, {})
        boards = entry.get("boards") if entry.get("date") == today else None
        if boards is None:
            boards, uncertain = _fetch_boards(company)
            if not uncertain:
                cache[key] = {"date": today, "boards": boards}
                cache = {k: v for k, v in cache.items() if k.startswith("v2:")}
                save_json(cache_path, cache)
    want = _title_tokens(title)
    if not want:
        return {"match": False, "reason": "empty title"}
    want_places, want_mode = _places(location), _mode(title, location)
    want_req = {m.group(1).upper() for m in REQ_ID.finditer(text or "")}
    cands, notes = [], []
    for b in boards:
        if not b.get("identity"):
            if b.get("jobs"):
                notes.append(f"{b['ats']}:{b['slug']} identity not confirmed")
            continue
        for j in b["jobs"]:
            if not j.get("title") or not j.get("url"):
                continue
            if _title_tokens(j["title"]) != want:
                continue
            have_places = _places(j.get("location", ""))
            if want_places and have_places and not (want_places & have_places):
                notes.append(f"'{j['title']}' location {j.get('location')!r} != {location!r}")
                continue
            have_mode = {"on-site": "onsite", "onsite": "onsite", "remote": "remote", "hybrid": "hybrid"}.get(j.get("workplace") or "") \
                or _mode(j["title"], j.get("location", ""))
            if want_mode and have_mode and want_mode != have_mode:
                notes.append(f"'{j['title']}' work mode {have_mode} != {want_mode}")
                continue
            if want_req and j.get("req") and j["req"].upper() not in want_req:
                notes.append(f"'{j['title']}' requisition {j['req']} not in {sorted(want_req)}")
                continue
            cands.append((b, j))
    if not cands:
        return {"match": False, "reason": "no identical posting on a confirmed employer board" + (f" ({'; '.join(notes[:3])})" if notes else "")}
    if len(cands) > 1:
        return {"match": False, "reason": f"ambiguous: {len(cands)} postings with the same title "
                + "; ".join(f"{j['title']} [{j.get('location')}]" for _, j in cands[:4])}
    b, j = cands[0]
    url = j["url"]
    return {"match": True, "ats": b["ats"], "apply_url": url.rstrip("/") + "/apply" if b["ats"] == "lever" else url,
            "matched_title": j["title"], "matched_location": j.get("location"), "posting_id": j.get("id"),
            "identity": b["identity"], "requisition_checked": bool(want_req and j.get("req"))}


def find_direct_route(company: str, title: str, location: str = "", text: str = "") -> dict | None:
    """Backward-compatible wrapper: the strict match result, or None when there is no unambiguous match."""
    r = match_direct_route(company, title, location, text)
    return r if r.get("match") else None


def check(url: str) -> dict:
    if m := re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url):
        code = _status_code(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}?mode=json")
        if code == 200:
            return {"open": True, "route": "ats_form", "apply_url": url.split("?")[0].rstrip("/") + "/apply", "source": "lever api"}
        if code == 404:
            return {"open": False, "route": None, "reason": "Lever API: posting not found (closed)", "source": "lever api"}
        return {"open": None, "route": None, "reason": f"Lever API HTTP {code}", "source": "lever api"}
    if m := re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url):
        code = _status_code(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
        if code == 200:
            return {"open": True, "route": "ats_form", "apply_url": url, "source": "greenhouse api"}
        if code == 404:
            return {"open": False, "route": None, "reason": "Greenhouse API: posting not found (closed)", "source": "greenhouse api"}
        return {"open": None, "route": None, "reason": f"Greenhouse API HTTP {code}", "source": "greenhouse api"}
    if "linkedin.com" in url:
        p = subprocess.run(["/opt/homebrew/bin/bun", "run", str(ROOT / ".agents/skills/linkedin-search/cli/src/cli.ts"),
                            "detail", url, "--format", "json"], capture_output=True, text=True, timeout=90, cwd=ROOT)
        if p.returncode != 0:
            return {"open": None, "route": None, "reason": "LinkedIn detail failed", "source": "linkedin cli",
                    "needs_browser_check": True}
        d = json.loads(p.stdout)
        d = d.get("result", d)
        text = d.get("description") or ""
        if not d.get("isActive", True) or CLOSED.search(text):
            return {"open": False, "route": None, "reason": "LinkedIn: closed banner / no longer accepting", "source": "linkedin cli"}
        direct = find_direct_route(d.get("company") or "", d.get("title") or "", d.get("location") or "", text)
        if direct:
            return {"open": True, "route": "ats_form", "apply_url": direct["apply_url"], "source": f"direct {direct['ats']} board",
                    "linkedin_url": url, "note": f"same job found on the employer's {direct['ats']} board ({direct['matched_title']})"}
        return {"open": None, "route": "linkedin", "apply_url": url, "source": "linkedin cli (signed-out view)",
                "needs_browser_check": True,
                "reason": "LinkedIn signed-out view looks open but can hide 'Not currently accepting applications'; "
                          "no direct Lever/Greenhouse posting found",
                "company": d.get("company"), "title": d.get("title")}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "p.html"
        p = subprocess.run([sys.executable, str(ROOT / "tools/fetch_page.py"), url, "--out", str(out)],
                           capture_output=True, text=True, timeout=90)
        if p.returncode != 0 or not out.exists():
            reason = (p.stdout + p.stderr).strip().splitlines()[-1:] or ["fetch failed"]
            if re.search(r"\b(404|410)\b", " ".join(reason)):
                return {"open": False, "route": None, "reason": f"page gone ({reason[0][:80]})", "source": "fetch_page"}
            return {"open": None, "route": None, "reason": reason[0][:120], "source": "fetch_page"}
        html = out.read_text(encoding="utf-8", errors="ignore")
    if CLOSED.search(re.sub(r"<[^>]+>", " ", html)):
        return {"open": False, "route": None, "reason": "page says applications are closed", "source": "fetch_page"}
    route = "web_form" if re.search(r"<form|apply|başvur", html, re.I) else "unknown"
    return {"open": True, "route": route, "apply_url": url, "source": "fetch_page"}


def record_closed(key: str, result: dict) -> None:
    data = load_json(SEEN, {"seen": {}})
    e = data["seen"].get(key)
    if e is not None:
        e["status"] = "expired"
        e["expired_reason"] = f"{result.get('reason')} (checked {now().date()})"
        save_json(SEEN, data)
    log_event("posting_closed", key=key, reason=result.get("reason", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--record")
    ap.add_argument("--mark-closed", metavar="REASON", help="record a closure seen in the signed-in browser")
    a = ap.parse_args()
    if a.mark_closed:
        res = {"open": False, "state": "closed", "route": None, "reason": a.mark_closed, "source": "signed-in browser"}
    else:
        res = check(a.url)
    res["state"] = {True: "open", False: "closed", None: "unknown"}[res["open"]]
    if a.record and res["open"] is False:
        record_closed(a.record, res)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["open"] else (3 if res["open"] is False else 4)


if __name__ == "__main__":
    raise SystemExit(main())
