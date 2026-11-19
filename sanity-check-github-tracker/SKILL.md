# Org Commit Monitor

## Setup
1. Place `monitor.py`, and `SKILL.md` in the skill folder.
2. get the list of member needed for this skill from the `monitor.py`
3. Set Environment Variables:
   - `GITHUB_TOKEN`: Your Personal Access Token.
   - `GITHUB_ORG`: Organization that owns the team (default `WeBuddhist`).
   - `GITHUB_ORGS`: Comma-separated orgs whose repos count as commits (default `WeBuddhist,OpenPecha,WeBuddhist-Data`). Used for both the daily list and "last commit N days ago".

## Automation
- **Cron**: `30 4 * * *` (Runs daily at 10:00 AM IST; adjust for UTC timezone offset)
- **Action**: Run `python3 monitor.py` and post output to #dev-updates.

## Commands
- `/team-status`: Runs the script immediately to show current standings.