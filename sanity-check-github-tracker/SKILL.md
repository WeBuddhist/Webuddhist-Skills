# Org Commit Monitor

## Setup
1. Place `monitor.py`, and `SKILL.md` in the skill folder.
2. get the list of member needed for this skill from the `monitor.py`
3. Set Environment Variables:
   - `GITHUB_TOKEN`: Classic Personal Access Token with `repo` + `read:org` scopes, from an account in every org below (SSO-authorized if the org uses SSO).
   - `DISCORD_WEBHOOK_URL`: When set, the script posts the report to this webhook. Leave unset to only print it.
   - `GITHUB_ORG`: Organization that owns the team (default `WeBuddhist`).
   - `GITHUB_ORGS`: Comma-separated orgs whose repos count as commits (default `WeBuddhist,OpenPecha,WeBuddhist-Data`). Used for both the daily list and "last commit N days ago".
   - `GITHUB_TEAM`: Team whose members are tracked (default `openpecha-dev-team`).
   - `MONITOR_LOOKBACK_DAYS`: How many days back to scan all branches for "last commit N days ago" (default 5). Older than that falls back to default-branch search.

## Automation
- **Render cron job** (`render.yaml` at the repo root): runs `python monitor.py` daily at `30 4 * * *` UTC (10:00 IST) and posts to Discord. Logs show in the Render dashboard.
- Render has no persistent disk for cron jobs, so `state.json` starts empty each run. The report does not depend on it: "last commit N days ago" comes from the all-branch lookback scan and GitHub search.
- **GitHub Action** (`.github/workflows/sanity-check-github-tracker.yml`): manual backup only, via Actions > Run workflow. Uses the `ORG_MONITOR_TOKEN` and `DISCORD_WEBHOOK_URL` repo secrets.

## Commands
- `/team-status`: Runs the script immediately to show current standings.
