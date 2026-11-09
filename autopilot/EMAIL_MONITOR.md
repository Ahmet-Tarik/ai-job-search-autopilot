# Shared email monitor (design - not enabled yet)

Status: **implemented for Phase 1** in `gmail_monitor.py` (launchd every 15 min, `gmail.readonly` OAuth via `gmail_auth.py`, no LLM). Phase 1 writes `email_*` events to `autopilot/logs/events.jsonl`. A future Phase 2 adds its own query and domain log on the same token, never a second mailbox reader. The interface below still holds.

## Shape

```
Gmail (read-only scope) --> email monitor --> classified events --> consumers
                                                 |-- Phase 1 (job search): autopilot/logs/events.jsonl
                                                 |-- Phase 2 (business):   phase2/logs/events.jsonl   (later)
                                                 '-- personal briefing combines both
```

- **One reader, many consumers.** The monitor fetches new messages (since a stored history id), classifies each into exactly one domain (`jobs`, `business`, `other`) and a type, and appends an event to that domain's log. It never acts on a message.
- **Read-only.** Gmail scope `gmail.readonly` only. No sending, replying, labelling, archiving, deleting or settings changes. The existing `/gmail-sync` skill already follows this pattern for the tracker; the monitor will wrap it rather than duplicate it.
- **Data, not instructions.** Email content is untrusted third-party text. Classification output is a type plus short quoted facts (company, date, subject), never a command.

## Phase 1 event types (domain `jobs`)
| Type | Effect (applied by the Claude daily run, not the monitor) |
|---|---|
| `application_confirmation` | Confirms a submission (second proof besides the confirmation page) |
| `interview_invitation` | `/outcome` stage update proposed; urgent Telegram alert |
| `recruiter_message` | Listed in the next briefing |
| `rejection` / `offer` | `/outcome` resolution proposed to the candidate |
| `followup_request` | Listed as a pending action |

## Phase 2 event types (domain `business`, later)
`customer_inquiry`, `business_opportunity`, `project_message`, `payment_notification`, `other_relevant`.

## Enabling later (checklist)
1. Candidate grants a Gmail connector with read-only scope (in Claude settings), never by sharing a password.
2. Add an `email_check` step to the daily AI run that calls `/gmail-sync` in read-only mode and logs the event types above.
3. Briefings already reserve an "Email" section; switch it from "not connected" to the event summary.
