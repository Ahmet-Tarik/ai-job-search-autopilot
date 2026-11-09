# Security policy

## Reporting
Please report security problems privately through GitHub's "Report a vulnerability" (Security tab) rather
than in a public issue.

## What must never be committed
`autopilot/config.json`, `autopilot/form_answers.json`, `autopilot/bench_gold.json`, `autopilot/state/`,
`autopilot/logs/`, `autopilot/reports/`, any `.env` file, and the Telegram token file in
`~/.config/job-search/`. All of these are listed in `.gitignore`. The CVs, cover letters, application
packets and tracker live in the ai-job-search workspace, which ignores them as well.

## Design principles
See [docs/security-model.md](docs/security-model.md).
