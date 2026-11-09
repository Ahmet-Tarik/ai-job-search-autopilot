# Security model

## Threats considered

| Threat | Mitigation |
|---|---|
| Prompt injection in job postings or emails | Postings pass the framework's integrity gate (`posting_guard.py`), and emails are treated as data. Prompts state that instructions inside them are ignored. Model output must match a JSON schema, and quotes must exist in the source. |
| Model hallucination causing a wrong rejection | Rejection only with a verified quote of a hard reason. Missing information is `unknown`. Every 3rd rejection is audited by Claude, and repeated misses disable the local model. |
| Fabricated CV or form content | Documents and form answers come only from the verified profile and a pre-approved answer file. Anything else pauses the application. |
| Acting without consent | No automatic submission or outbound messages. Telegram authorizes preparation only, and submission needs an explicit chat approval per packet plus a live form check. Calendar events are created only on request. |
| Replying to an old Telegram list selects the wrong job | Published lists are immutable and indexed by message ID. |
| Applying through a look-alike route | Strict direct-route matching (identity, identical title, location, work mode, requisition ID, single match). |
| Credential leakage | Secrets live outside the repository (`~/.config`, mode 600), are never printed, and are git-ignored. |
| Data loss | Verified, timestamped, never-overwritten Drive snapshots with a restore procedure. Failures are reported. |
| Resource exhaustion on the user's machine | Memory-pressure guard, one model at a time, bounded input, model unloaded after each run. |

## Permissions

- **Gmail:** read-only (`search_threads`, and `get_thread` for matched interview or information requests).
- **Calendar:** read-only during monitoring. Events are created only after "add calendar proposal N".
- **Google Drive:** writes only new snapshot folders in a private folder, and never deletes.
- **Websites:** robots.txt respected, honest user agent, no CAPTCHA solving, no login automation, no
  header spoofing.
- **Repository protection:** a pre-tool hook blocks pushes, publishing and outbound messages without the
  candidate's explicit approval (part of the private workspace, not this repository).

## Data that never belongs in this repository

The real candidate profile and contact details, CVs and cover letters, application packets, tracker, email
content, research notes, logs, state, benchmark gold sets built from real postings, and any credential.
