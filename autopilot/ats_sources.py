"""Official employer job boards via public ATS APIs (Lever, Greenhouse, Workable).

These endpoints are published by the ATS vendors for exactly this purpose (job
listings on third-party sites), need no login and return only public postings.
A posting found here is verified: it comes from the employer's own board.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.request

UA = {"User-Agent": "job-search-autopilot/1.0 (personal job search; contact via candidate)"}


def _get(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _ms(ts) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(ts) / 1000, dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def lever(slug: str) -> list[dict]:
    out = []
    for p in _get(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        cat = p.get("categories") or {}
        loc = cat.get("location") or ", ".join(cat.get("allLocations") or [])
        wp = (p.get("workplaceType") or "").lower()
        out.append({"title": p.get("text"), "company": slug, "url": p.get("hostedUrl"),
                    "location": f"{loc} ({wp})" if wp else loc, "date": _ms(p.get("createdAt")),
                    "work_mode": wp, "_apply_url": p.get("applyUrl")})
    return out


def greenhouse(slug: str) -> list[dict]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    out = []
    for p in data.get("jobs", []):
        out.append({"title": p.get("title"), "company": slug, "url": p.get("absolute_url"),
                    "location": (p.get("location") or {}).get("name", ""),
                    "date": (p.get("updated_at") or "")[:10] or None, "work_mode": ""})
    return out


def workable(slug: str) -> list[dict]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    name = data.get("name") or slug
    out = []
    for p in data.get("jobs", []):
        remote = p.get("telecommuting")
        loc = ", ".join(x for x in (p.get("city"), p.get("country")) if x)
        out.append({"title": p.get("title"), "company": name, "url": p.get("shortlink") or p.get("url"),
                    "location": f"{loc} (remote)" if remote else loc,
                    "date": (p.get("published_on") or p.get("created_at") or "")[:10] or None,
                    "work_mode": "remote" if remote else ""})
    return out


FETCHERS = {"lever": lever, "greenhouse": greenhouse, "workable": workable}
DISPLAY = {  # nicer company names for the tracker and briefings
    "insiderone": "Insider One", "trendyol": "Trendyol", "picus": "Picus Security", "peakgames": "Peak",
    "peak": "Peak", "dreamgames": "Dream Games", "getmidas": "Midas", "iyzico": "iyzico",
    "appnation": "AppNation", "commencis": "Commencis", "udemy": "Udemy", "gram": "Gram Games",
}


def fetch_all(cfg: dict, pause: float = 1.0) -> tuple[list[dict], list[str]]:
    hits, errors = [], []
    loc_re = re.compile(cfg.get("location_must_match", "."), re.I)
    cutoff = (dt.date.today() - dt.timedelta(days=cfg.get("max_age_days", 45))).isoformat()
    for kind, fetch in FETCHERS.items():
        for slug in cfg.get(kind, []):
            try:
                rows = fetch(slug)
            except Exception as exc:  # noqa: BLE001 - one board failing must not stop the run
                errors.append(f"{kind}:{slug}: {type(exc).__name__}")
                continue
            for r in rows:
                if not r.get("url") or not r.get("title"):
                    continue
                if not loc_re.search(f"{r.get('location', '')} {r.get('work_mode', '')}"):
                    continue
                if r.get("date") and r["date"] < cutoff:
                    continue
                r["company"] = DISPLAY.get(r["company"], r["company"])
                r["_portal"] = f"ats-{kind}"
                hits.append(r)
            time.sleep(pause)
    return hits, errors
