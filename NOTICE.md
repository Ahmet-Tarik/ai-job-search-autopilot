# Notice

This project is an add-on to **ai-job-search** (https://github.com/ai-job-search-dev/ai-job-search),
Copyright (c) 2026 Mads Lorentzen and contributors, released under the MIT License.

The autopilot calls, but does not include, these parts of ai-job-search:
the portal search CLIs (`.agents/skills/linkedin-search`, `.agents/skills/freehire-search`), `tools/`
(`scrape_state.py`, `rank_state.py`, `posting_guard.py`, `fetch_page.py`), and the Claude skills `/rank` and
`/apply` with their `posting-scorer` and `application-reviewer` agents and LaTeX CV and cover-letter templates.
Developed and tested against ai-job-search commit `2b3f613` (2026-11-08).
