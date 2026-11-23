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

## Report
- **Active**: everyone with commits on the previous day, per repo and branch.
- **Developers who need to commit more**: one line each with their last commit — days ago, date, repo @ branch, message and a link.
- **Activity chart**: a PNG heatmap (developer × day, commit counts, last `MONITOR_LOOKBACK_DAYS` days, all branches) posted after the text and saved as `activity.png`. Needs `matplotlib`; without it the text report still goes out.

## Automation
- **Render cron job** (`render.yaml` at the repo root): runs `python monitor.py` daily at `30 3 * * *` UTC (09:00 IST) and posts to Discord. Logs show in the Render dashboard.
- Render has no persistent disk for cron jobs, so `state.json` starts empty each run. The report does not depend on it: "last commit N days ago" comes from the all-branch lookback scan and GitHub search.
- To run it by hand, use **Trigger Run** on the Render cron job, or run `python monitor.py` locally with the variables above set.

## Commands
- `/team-status`: Runs the script immediately to show current standings.
