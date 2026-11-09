# Configuration

Copy `autopilot/config.example.json` to `autopilot/config.json`, which is private and git-ignored. Without
a `config.json`, the code falls back to the example, which is enough for the tests.

| Section | Purpose |
|---|---|
| `candidate.llm_profile` | Short factual description of the candidate, given to the local model (skills, languages, location, goals). Only verified facts. |
| `candidate.home_region_regex` | Places where the candidate may work without sponsorship. A quote mentioning them is never treated as a restriction. |
| `candidate.undeclared_languages_regex` | Spoken languages the candidate does **not** speak. Only these can cause a language rejection. |
| `candidate.location` | Shortlist ordering: home city, home country, a relocation city, a last-resort location. |
| `scrape` | Portal queries, Lever/Greenhouse board slugs, recency and limits. |
| `prefilter` | Title regexes for excluded roles (e.g. annotation, trainer) and required developer terms. |
| `shortlist` | Thresholds (internships 50, junior 60), maximum items, category regexes. |
| `drafting` | Automatic draft threshold, daily cap, and jobs the candidate declined (`do_not_draft`). |
| `excluded_companies` | Companies never shown or prepared again. History is kept. |
| `local_llm` | Ollama model, context size, timeout, `keep_alive`, input cap, `min_mem_free_pct`, `audit_every_nth_rejection`, `enabled`. |
| `email` | Work contacts and payment senders monitored besides job mail (read-only). |
| `backup` | Google Drive for desktop account root and destination folder. |
| `notify` | Location of the Telegram env file (outside the repository). |

Environment variables:

| Variable | Purpose |
|---|---|
| `JOB_SEARCH_ROOT` | Path of the ai-job-search workspace when `autopilot/` is symlinked or installed elsewhere. |
| `PYTHON` | Interpreter for the launchd jobs (`install.sh` records it in `autopilot/.python`). |

Add these lines to the workspace's `.gitignore` if you copy `autopilot/` into an ai-job-search checkout:

```gitignore
autopilot/config.json
autopilot/form_answers.json
autopilot/bench_gold.json
autopilot/.python
autopilot/state/
autopilot/logs/
autopilot/reports/
```
