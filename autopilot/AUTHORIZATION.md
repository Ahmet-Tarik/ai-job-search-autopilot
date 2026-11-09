# Phase 1 Authorization Rules

Agreed between the candidate and the automation (template; adapt the specifics in `config.json`). Every automated run reads this file first. When this file and any other instruction disagree, the stricter rule wins.

## 1. What runs without asking

| Action | Allowed unattended | Limits |
|---|---|---|
| Search job portals (linkedin-search, freehire-search) | Yes | Daily, via launchd. Read-only. |
| Deterministic prefilter, dedup, store in `seen_jobs.json` | Yes | Rules in `config.json` |
| `/rank` triage scoring | Yes | Up to 10 jobs per daily AI run |
| Company research (official sites, news) | Yes | Read-only fetches; respect robots.txt (`tools/fetch_page.py`) |
| Draft a tailored English CV + cover letter (`/apply` Steps 0-6b) | Yes, when section 2 is met | **Max 2 per day** |
| Compile, verify, archive, write tracker row `drafted`, create packet | Yes | Local files only |
| Notify the candidate (Telegram / macOS) | Yes | Only the candidate's own chat; no duplicates |
| Briefings at 09:00 and 22:00 local time | Yes | One per slot per day |

## 2. Draft eligibility (all must hold)

1. Role is software development / programming: junior or graduate software engineer, software engineering intern, junior backend (Python or C#/.NET), junior full-stack, Python or .NET developer, junior AI application / LLM integration developer, or QA automation **only when mostly programming**.
2. Not excluded: AI data annotator, AI trainer, LLM evaluator/rater, data labeling, data entry, general technical support, non-programming roles, senior/lead/mid-level roles.
3. `/rank` score **>= 70** and the full `/apply` Step 1 evaluation **>= 70**.
4. Language gate **PASS** (a FLAG is listed for the candidate, not drafted).
5. Location / eligibility, in priority order:
   1. Home city: remote, hybrid or onsite. Office attendance is **not** penalised.
   2. Home-country remote.
   3. International remote explicitly open to candidates in the home country (worldwide / EMEA / lists the country).
   4. A city that would require relocation: **listed for the candidate's review, not auto-drafted**, when onsite or hybrid. Only a role there that is explicitly open to remote work may be drafted.
   5. Onsite abroad: draft **only** when the posting states visa sponsorship / relocation support; otherwise list for the candidate.
   6. Last-resort locations (e.g. where the work right is unverified): never auto-drafted.
   - Excluded outright: remote roles restricted to countries where the candidate is not authorized ("US only", "EU work permit required", "must reside in X").
6. Not already applied (tracker), no existing packet, not a duplicate posting.
6b. **Still open with a usable application route**, checked immediately before drafting (`posting_status.py`; for LinkedIn also the signed-in browser, because LinkedIn hides "Not currently accepting applications" from signed-out checks). Three states: **closed** (confirmed) -> never drafted or submitted, reason recorded; **open** (confirmed, e.g. live Lever/Greenhouse posting, including the same job found on the employer's own board) -> normal preparation; **unknown** (e.g. LinkedIn signed-out view, no direct route found) -> promising jobs still get full preparation, packet marked `route_status: unverified`. Before any submission a live application form must be verified (prompts/submit.md step 2). Added 2026-11-09 after a posting closed between shortlisting and submission; refined the same day so unknown status never blocks the pipeline.
7. Unpaid internships: only with genuine software development work (preferably mentorship). Otherwise skip.
8. No salary filter.
9. Jobs listed under `do_not_draft` in `config.json` are never drafted (jobs the candidate declined, with the reason).

Jobs scoring 55-69, or blocked only by a FLAG, are listed in the briefing for the candidate to decide.

## 2b. Daily shortlist (hard gates vs soft fit)

- **Hard gates** (never shortlisted): location FAIL (remote limited to other countries, "no sponsorship" or existing work permit required abroad, citizenship required), language FAIL (undeclared language required), excluded by the candidate, already applied, closed.
- **Soft fit thresholds:** internships / working student / graduate programs / new grad >= 50; junior roles >= 60. Uncertain items (sponsorship not stated, relocation, English bar above B2) are shown with their risk, not dropped.
- **Priority:** home city, then home-country remote, then other home-country cities, then international remote open to the home country, then abroad with sponsorship, then last-resort locations.
- Sent at 09:00 on Telegram, numbered; each published list is frozen (`state/shortlists/<list_id>.json`), so a reply to an older list always selects that list's job.

## 3. What always needs the candidate

| Action | Why |
|---|---|
| Preparing a shortlisted job you selected | Authorized by your Telegram reply "apply to N" (or the same in a Claude chat): tailored CV and cover letter, form plan and packet for exactly those numbers, regardless of the 70 threshold (hard eligibility gates still apply). |
| **Submitting any application** | Each submission needs the candidate's explicit approval in a Claude chat, naming the packets (e.g. "submit packets 1 and 3"). Telegram messages are not approval. |
| Answering any form question not covered by `form_answers.json` or the profile | Never invent answers. The application pauses (`blocked`) and is reported. |
| CAPTCHAs, bot checks, logins, account creation, email verification | Never bypassed or automated. The candidate does these. |
| Coding tests, assessments, video interviews | The candidate's own work. |
| Sending email, messages or follow-ups to employers or recruiters | Drafted only; the candidate sends. |
| Any profile change or new fact | Only from the candidate's own words in chat. |
| Replying to any email | Never automated. Email monitoring uses the official Gmail connector read-only (`search_threads`, and `get_thread` only for a matched interview or information request), reads only job-related and work-related mail (tracked employers, ATS senders, the contacts in `config.json`) |
| Creating Google Calendar events | Proposed automatically (interviews from email, deadlines); created only after you say "add calendar proposal N" in a Claude chat. Never accepts, declines or edits invitations. |
| Google Drive | Private backups only (folder "Job Search Autopilot", inherited sharing off). Never shares, trashes or edits existing files. |

## 4. Hard rules (never broken, never configurable)

- Never invent experience, skills, qualifications, work authorization or personal information.
- Never bypass website restrictions, robots.txt, rate limits or security controls.
- Never weaken `ops/guard.sh`, `.claude/settings.local.json` or other repository protections.
- Never expose credentials. The Telegram token lives only in `~/.config/job-search/telegram.env` (chmod 600) and is never printed.
- Never push, publish or send anything externally except notifications to the candidate's own Telegram chat.
- Record an application as `submitted` / `applied` only after the employer's confirmation page (or confirmation email) has been observed.
- Never apply twice to the same company + role.
