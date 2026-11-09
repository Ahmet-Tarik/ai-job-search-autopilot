#!/usr/bin/env python3
r"""Application packets: the hand-off between drafting and submission.

A packet is documents/applications/<slug>/packet.json written by the Claude
daily run after /apply produced verified PDFs. Status lifecycle:

  ready_for_approval -> approved -> submitting -> submitted
                                 \-> blocked (CAPTCHA, login, unusual question, ...)
  ready_for_approval -> declined (candidate said no)
  any open status    -> closed (posting closed / no application route; history kept)

Only the candidate's explicit approval in a Claude chat moves a packet to
"approved". "submitted" is set only after the employer's confirmation page
was observed (see prompts/submit.md).

Usage:
  packets.py list [--status STATUS]
  packets.py create SLUG --company C --role R --url URL --score N --cv PDF [--cover PDF] [--notes TEXT]
                     [--route-status verified|unverified] [--linkedin-url URL]
  packets.py set SLUG STATUS [--detail TEXT]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import APPS, load_json, log_event, now, save_json  # noqa: E402

STATUSES = {"ready_for_approval", "approved", "submitting", "submitted", "blocked", "declined", "closed"}


def all_packets() -> list[dict]:
    out = []
    for path in sorted(APPS.glob("*/packet.json")):
        data = load_json(path, None)
        if data:
            data["slug"] = path.parent.name
            out.append(data)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    cr = sub.add_parser("create")
    cr.add_argument("slug")
    for f in ("--company", "--role", "--url", "--cv"):
        cr.add_argument(f, required=True)
    cr.add_argument("--score", type=int, required=True)
    cr.add_argument("--cover")
    cr.add_argument("--notes", default="")
    cr.add_argument("--route-status", choices=["verified", "unverified"], default="verified",
                    help="unverified = application route not confirmed yet; must be verified before submission")
    cr.add_argument("--linkedin-url", default="")
    st = sub.add_parser("set")
    st.add_argument("slug")
    st.add_argument("status", choices=sorted(STATUSES))
    st.add_argument("--detail", default="")
    a = ap.parse_args()

    if a.cmd == "list":
        rows = [p for p in all_packets() if not a.status or p["status"] == a.status]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    folder = APPS / a.slug
    path = folder / "packet.json"
    if a.cmd == "create":
        if path.exists():
            print(f"packet exists: {path}", file=sys.stderr)
            return 1
        folder.mkdir(parents=True, exist_ok=True)
        next_id = max([p.get("id", 0) for p in all_packets()] or [0]) + 1
        packet = {
            "id": next_id,
            "company": a.company, "role": a.role, "url": a.url, "score": a.score,
            "cv_pdf": a.cv, "cover_pdf": a.cover, "notes": a.notes,
            "route_status": a.route_status, "linkedin_url": a.linkedin_url,
            "status": "ready_for_approval", "created": now().isoformat(timespec="seconds"),
            "history": [],
        }
        save_json(path, packet)
        log_event("packet_ready", slug=a.slug, company=a.company, role=a.role, score=a.score, url=a.url)
        print(f"created {path}")
        return 0

    packet = load_json(path, None)
    if packet is None:
        print(f"no packet: {path}", file=sys.stderr)
        return 1
    if a.status == "submitted" and packet["status"] not in {"approved", "submitting"}:
        print("refusing: a packet can only become submitted after approval", file=sys.stderr)
        return 1
    packet["history"].append({"ts": now().isoformat(timespec="seconds"),
                              "from": packet["status"], "to": a.status, "detail": a.detail})
    packet["status"] = a.status
    save_json(path, packet)
    kind = {"submitted": "application_submitted", "blocked": "blocker",
            "approved": "packet_approved", "declined": "packet_declined"}.get(a.status, "packet_status")
    log_event(kind, slug=a.slug, company=packet["company"], role=packet["role"],
              status=a.status, detail=a.detail)
    print(f"{a.slug}: {a.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
