import os
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
from datetime import datetime, timedelta, timezone


ORG = os.getenv("GITHUB_ORG", "WeBuddhist")
# Orgs whose repos count as team activity. The team itself is read from ORG.
ORGS = [
    o.strip()
    for o in os.getenv("GITHUB_ORGS", f"{ORG},OpenPecha,WeBuddhist-Data").split(",")
    if o.strip()
] or [ORG]
WINDOW_DAYS = 1
# How far back to scan all branches when working out "last commit N days ago".
LOOKBACK_DAYS = int(os.getenv("MONITOR_LOOKBACK_DAYS", "5"))
TEAM_SLUG = os.getenv("GITHUB_TEAM", "openpecha-dev-team")
_ISO_TZ_OFFSET = "+00:00"
# A "day" runs 01:00 IST to 01:00 IST the next day.
_DAY_TZ = timezone(timedelta(hours=5, minutes=30))
_DAY_START_HOUR = 1
# (connect, read) avoids hanging indefinitely on slow TLS/handshake vs slow body.
_REQUEST_TIMEOUT = (15, 120)
# Initial request + 2 retries for transient API failures.
_GET_JSON_MAX_ATTEMPTS = 3
_RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504})

logger = logging.getLogger(__name__)


def _api_headers():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "Set GITHUB_TOKEN in your environment (Personal Access Token with repo scope)."
        )
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2026-03-10",
    }


def get_team():
    headers = _api_headers()
    logger.info("Fetching team members org=%s team=%s", ORG, TEAM_SLUG)
    url = f"https://api.github.com/orgs/{ORG}/teams/{TEAM_SLUG}/members"
    res = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
    res.raise_for_status()
    members = [user["login"] for user in res.json()]
    logger.info("Fetched %d team members: %s", len(members), members)
    return members


def load_data(file, default):
    if os.path.exists(file):
        with open(file, "r") as f:
            return json.load(f)
    return default


def _work_day(dt):
    """Calendar day a timestamp belongs to, with days starting 01:00 IST."""
    return (dt.astimezone(_DAY_TZ) - timedelta(hours=_DAY_START_HOUR)).date()


def _today_work_day():
    return _work_day(datetime.now(timezone.utc))


def commit_window_bounds():
    """
    Last WINDOW_DAYS days (01:00 IST boundaries), excluding the day of execution.
    Returns (start_date, end_date_exclusive) as date objects,
    and a range string for display (same as former GitHub committer-date search).
    """
    today = _today_work_day()
    end = today  # exclusive upper bound (today is not included)
    start = today - timedelta(days=WINDOW_DAYS)
    last_inclusive = end - timedelta(days=1)
    date_range = f"{start.isoformat()}..{last_inclusive.isoformat()}"
    return start, end, date_range


def _window_to_since_until_iso(window_start, window_end_exclusive):
    """UTC ISO timestamps for GET /repos/.../commits (since / until)."""
    since_dt = _day_start_utc(window_start)
    until_dt = _day_start_utc(window_end_exclusive)
    return (
        since_dt.isoformat().replace(_ISO_TZ_OFFSET, "Z"),
        until_dt.isoformat().replace(_ISO_TZ_OFFSET, "Z"),
    )


def _day_start_utc(d):
    """Start of day d (01:00 IST) as a UTC datetime."""
    start = datetime(d.year, d.month, d.day, _DAY_START_HOUR, 0, 0, tzinfo=_DAY_TZ)
    return start.astimezone(timezone.utc)


def _parse_github_iso_datetime(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", _ISO_TZ_OFFSET))
    except ValueError:
        return None


def _repo_pushed_before_window(repo, window_start_date):
    """
    True if repo's last push was strictly before the commit window start.
    Unknown/missing pushed_at -> False (still scan). Heuristic: can miss rare
    backdated commits pushed before the window.
    """
    pushed = _parse_github_iso_datetime(repo.get("pushed_at"))
    if pushed is None:
        return False
    window_start = _day_start_utc(window_start_date)
    return pushed < window_start


def _get_json_allow_missing(url, headers, params=None):
    """GET JSON; return None on 403/404 so one repo cannot stop the run."""
    params = params or {}
    for attempt in range(_GET_JSON_MAX_ATTEMPTS):
        try:
            r = requests.get(
                url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT
            )
        except requests.RequestException as e:
            if attempt < _GET_JSON_MAX_ATTEMPTS - 1:
                time.sleep(1 + attempt)
                continue
            logger.warning(
                "skipped (request error after %s tries): %s (%r)",
                _GET_JSON_MAX_ATTEMPTS,
                url,
                e,
            )
            return None

        if r.status_code in (403, 404):
            return None
        if r.ok:
            return r.json()
        if r.status_code in _RETRYABLE_HTTP:
            if attempt < _GET_JSON_MAX_ATTEMPTS - 1:
                time.sleep(1 + attempt)
                continue
            logger.warning(
                "skipped (HTTP %s after %s tries): %s",
                r.status_code,
                _GET_JSON_MAX_ATTEMPTS,
                url,
            )
            return None
        r.raise_for_status()
        return r.json()


def get_org_repo_total(headers, org):
    """
    Approximate total repos in the org (public + private when the API exposes both).
    Used only to report repos_skipped_stale_push after early exit.
    """
    data = _get_json_allow_missing(f"https://api.github.com/orgs/{org}", headers)
    if not data:
        return None
    pub = data.get("public_repos") or 0
    priv = data.get("total_private_repos")
    if priv is None:
        return pub
    return pub + priv


def _iter_org_repos(headers, org):
    page = 1
    while True:
        data = _get_json_allow_missing(
            f"https://api.github.com/orgs/{org}/repos",
            headers,
            {
                "per_page": 100,
                "page": page,
                "type": "all",
                "sort": "pushed",
                "direction": "desc",
            },
        )
        if not data:
            if page == 1:
                logger.warning(
                    "No repos visible in org %s — token may lack access (SSO?)", org
                )
            break
        yield from data
        if len(data) < 100:
            break
        page += 1


def _iter_repo_branches(headers, owner, repo):
    page = 1
    while True:
        data = _get_json_allow_missing(
            f"https://api.github.com/repos/{owner}/{repo}/branches",
            headers,
            {"per_page": 100, "page": page},
        )
        if not data:
            break
        yield from data
        if len(data) < 100:
            break
        page += 1


def _committer_date(commit_obj):
    commit = commit_obj.get("commit") or {}
    committer = commit.get("committer") or {}
    dt = _parse_github_iso_datetime(committer.get("date"))
    return _work_day(dt) if dt else None


def _commit_author_login(commit_obj):
    """GitHub user login for the commit author, if present (matches `author` API filter)."""
    author = commit_obj.get("author")
    if not author:
        return None
    return author.get("login")


def _paginate_commits_for_branch(
    headers, owner, repo, branch_name, since_iso, until_iso, author=None
):
    """
    Newest-first commits reachable from branch_name, optionally filtered by author.
    Stops early once the page is entirely before the window (descending order).
    """
    page = 1
    while True:
        params = {
            "sha": branch_name,
            "since": since_iso,
            "until": until_iso,
            "per_page": 100,
            "page": page,
        }
        if author:
            params["author"] = author
        batch = _get_json_allow_missing(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            headers,
            params,
        )
        if not batch:
            break
        yield from batch
        if len(batch) < 100:
            break
        oldest = _committer_date(batch[-1])
        if oldest is not None:
            window_start = _work_day(_parse_github_iso_datetime(since_iso))
            if oldest < window_start:
                break
        page += 1


def fetch_team_commit_days_all_branches(members, window_start, window_end_exclusive):
    """
    GitHub /search/commits only indexes the default branch. To count activity on
    any branch, list commits per branch with since/until (one pass for the whole
    team), attribute by author login, dedupe by SHA per user.

    Org repos are listed with sort=pushed desc. On the first repo whose
    pushed_at is before the commit window start, we stop: all later
    repos are older or equal and are omitted (saves API calls). Rare false
    negatives if commits are backdated and pushed before the window.

    Returns (user -> days_with_commits_iso_set, user -> meta_dict).
    """
    headers = _api_headers()
    since_iso, until_iso = _window_to_since_until_iso(
        window_start, window_end_exclusive
    )
    team_logins = set(members)
    seen_shas = {u: set() for u in members}
    days = {u: set() for u in members}
    # Per user: full_name -> branch -> count (first branch wins for duplicate SHAs)
    breakdown = {u: defaultdict(lambda: defaultdict(int)) for u in members}
    repos_scanned = 0
    branch_requests = 0
    skipped_repos = 0
    repos_skipped_stale_push = 0

    for org in ORGS:
        logger.info("Fetching commits from org %s (one pass for all team members)", org)
        org_scanned = 0
        stale_cutoff = False
        for repo in _iter_org_repos(headers, org):
            full = repo.get("full_name") or ""
            if "/" not in full:
                continue
            if _repo_pushed_before_window(repo, window_start):
                stale_cutoff = True
                logger.info(
                    "Stale push cutoff — remaining repos omitted (sort=pushed desc): "
                    "%s pushed_at=%s",
                    full,
                    repo.get("pushed_at"),
                )
                break
            org_scanned += 1
            repos_scanned += 1
            logger.info("repo %s: %s", repos_scanned, full)
            n_branches = _scan_repo(
                headers, full, since_iso, until_iso, window_start,
                window_end_exclusive, team_logins, seen_shas, days, breakdown,
            )
            branch_requests += n_branches
            if n_branches == 0:
                skipped_repos += 1
        if stale_cutoff:
            total = get_org_repo_total(headers, org)
            if total is not None and repos_skipped_stale_push is not None:
                repos_skipped_stale_push += max(0, total - org_scanned)
            else:
                repos_skipped_stale_push = None

    def _breakdown_plain(u):
        out = {}
        for repo in sorted(breakdown[u].keys()):
            br = breakdown[u][repo]
            out[repo] = {b: br[b] for b in sorted(br.keys())}
        return out

    shared_meta = {
        "mode": "all_branches",
        "orgs": ORGS,
        "repos_scanned": repos_scanned,
        "branch_requests": branch_requests,
        "repos_with_no_branches": skipped_repos,
        "repos_skipped_stale_push": repos_skipped_stale_push,
        "since": since_iso,
        "until": until_iso,
    }
    meta_by_user = {
        u: {
            **shared_meta,
            "unique_commits_in_window": len(seen_shas[u]),
            "commit_breakdown": _breakdown_plain(u),
        }
        for u in members
    }
    return days, meta_by_user


def _scan_repo(
    headers, full, since_iso, until_iso, window_start, window_end_exclusive,
    team_logins, seen_shas, days, breakdown,
):
    """Count team commits on every branch of one repo. Returns branches scanned."""
    owner, name = full.split("/", 1)
    scanned = 0
    for br in _iter_repo_branches(headers, owner, name):
        branch_name = br.get("name")
        if not branch_name:
            continue
        scanned += 1
        for commit_obj in _paginate_commits_for_branch(
            headers, owner, name, branch_name, since_iso, until_iso
        ):
            login = _commit_author_login(commit_obj)
            if not login or login not in team_logins:
                continue
            sha = commit_obj.get("sha")
            if not sha or sha in seen_shas[login]:
                continue
            seen_shas[login].add(sha)
            d = _committer_date(commit_obj)
            if d is None:
                continue
            if window_start <= d < window_end_exclusive:
                days[login].add(d.isoformat())
                breakdown[login][full][branch_name] += 1
    return scanned


def _breakdown_rows_sorted_by_commit_count(bd):
    """(repo, branch, n) sorted by n descending, then repo and branch name."""
    rows = []
    for repo, branches in bd.items():
        for branch, n in branches.items():
            rows.append((repo, branch, n))
    rows.sort(key=lambda x: (-x[2], x[0], x[1]))
    return rows


def _commit_info(commit_obj, repo_full, branch):
    """Summary of one commit for the report: when, where, first message line, link."""
    commit = commit_obj.get("commit") or {}
    when = _parse_github_iso_datetime((commit.get("committer") or {}).get("date"))
    if when is None:
        return None
    message = (commit.get("message") or "").strip().splitlines()
    return {
        "when": when,
        "date": _work_day(when),
        "repo": repo_full,
        "branch": branch,
        "message": message[0][:80] if message else "",
        "url": commit_obj.get("html_url"),
    }


def _scan_lookback(headers, logins, end_exclusive, lookback_days):
    """
    One pass over every branch of every repo in ORGS pushed in the last
    lookback_days. Catches feature-branch work that /search/commits (default
    branches only) misses.

    Returns (login -> newest commit info, login -> {day_iso: commit count}).
    """
    logins = set(logins)
    newest = {}
    daily = {u: defaultdict(int) for u in logins}
    seen = {u: set() for u in logins}
    start = end_exclusive - timedelta(days=lookback_days)
    since_iso, until_iso = _window_to_since_until_iso(start, end_exclusive)
    logger.info(
        "Lookback scan: last %s days, all branches, for %d members",
        lookback_days,
        len(logins),
    )
    scanned = 0
    for org in ORGS:
        for repo in _iter_org_repos(headers, org):
            full = repo.get("full_name") or ""
            if "/" not in full:
                continue
            if _repo_pushed_before_window(repo, start):
                break
            scanned += 1
            logger.info("lookback repo %s: %s", scanned, full)
            owner, name = full.split("/", 1)
            for br in _iter_repo_branches(headers, owner, name):
                branch_name = br.get("name")
                if not branch_name:
                    continue
                for commit_obj in _paginate_commits_for_branch(
                    headers, owner, name, branch_name, since_iso, until_iso
                ):
                    login = _commit_author_login(commit_obj)
                    if login not in logins:
                        continue
                    sha = commit_obj.get("sha")
                    if not sha or sha in seen[login]:
                        continue
                    seen[login].add(sha)
                    info = _commit_info(commit_obj, full, branch_name)
                    if info is None:
                        continue
                    if start <= info["date"] < end_exclusive:
                        daily[login][info["date"].isoformat()] += 1
                    if login not in newest or info["when"] > newest[login]["when"]:
                        newest[login] = info
    logger.info(
        "Lookback scan (%s days) newest commits: %s",
        lookback_days,
        {u: i["date"].isoformat() for u, i in newest.items()},
    )
    return newest, daily


def _search_last_commit(headers, user):
    """
    Most recent commit by user across ORGS via /search/commits (default branches
    only), as commit info, or None if nothing is found.
    """
    found = []
    for org in ORGS:
        data = _get_json_allow_missing(
            "https://api.github.com/search/commits",
            headers,
            {
                "q": f"author:{user} org:{org}",
                "sort": "committer-date",
                "order": "desc",
                "per_page": 1,
            },
        )
        items = (data or {}).get("items") or []
        if items:
            repo_full = (items[0].get("repository") or {}).get("full_name") or org
            info = _commit_info(items[0], repo_full, None)
            if info:
                found.append(info)
    return max(found, key=lambda i: i["when"]) if found else None


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _display_name(user):
    return f"**{user[:1].upper()}{user[1:]}**"


def _days_ago_text(last_date, today):
    if last_date is None:
        return "no commits found"
    days = (today - last_date).days
    if days <= 0:
        return "last commit today"
    return f"last commit {_plural(days, 'day')} ago"


def _last_commit_line(user, info, today):
    """One report line for an inactive developer, with their last commit if known."""
    if info is None:
        return f"- {_display_name(user)} — no commits found"
    parts = [f"{_days_ago_text(info['date'], today)} ({info['date'].strftime('%a %d %b')})"]
    if info.get("repo"):
        where = f"`{info['repo']}`"
        if info.get("branch"):
            where += f" @ `{info['branch']}`"
        parts.append(where)
    if info.get("message"):
        msg = info["message"].replace("`", "'").replace("*", "").replace("_", " ")
        parts.append(f'"{msg}"')
    if info.get("url"):
        # <...> stops Discord from unfurling a preview card for every link.
        parts.append(f"[view](<{info['url']}>)")
    return f"- {_display_name(user)} — " + " · ".join(parts)


def _state_commit_info(state_user):
    """Last commit saved by an earlier run, if state.json survived (local runs)."""
    saved = state_user.get("last_commit") or {}
    raw_date = saved.get("date") or state_user.get("last_commit_date")
    if not raw_date:
        return None
    d = datetime.fromisoformat(raw_date).date()
    return {
        "when": datetime(d.year, d.month, d.day, tzinfo=_DAY_TZ),
        "date": d,
        "repo": saved.get("repo"),
        "branch": saved.get("branch"),
        "message": saved.get("message") or "",
        "url": saved.get("url"),
    }


# Dark chart surface (Discord is mostly viewed in dark mode) and a one-hue blue
# ramp, dark -> bright as commit counts rise; zero is the neutral gray.
_CHART_SURFACE = "#1a1a19"
_CHART_ZERO = "#383835"
_CHART_INK = "#e8e6e1"
_CHART_MUTED = "#a3a19b"
_CHART_BINS = [  # (lowest count, fill, cell text colour, legend label)
    (1, "#184f95", _CHART_INK, "1"),
    (2, "#2a78d6", _CHART_INK, "2–3"),
    (4, "#6da7ec", _CHART_SURFACE, "4–6"),
    (7, "#b7d3f6", _CHART_SURFACE, "7+"),
]


def _chart_bin(n):
    if n <= 0:
        return _CHART_ZERO, _CHART_MUTED
    fill, text = _CHART_BINS[0][1], _CHART_BINS[0][2]
    for low, f, t, _ in _CHART_BINS:
        if n >= low:
            fill, text = f, t
    return fill, text


def build_activity_chart(members, daily, days):
    """
    Heatmap PNG: one row per developer, one column per day, cell = commits.
    Returns PNG bytes, or None if matplotlib is unavailable.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch, Rectangle
    except ImportError:
        logger.warning("matplotlib not installed; skipping activity chart")
        return None
    import io

    day_keys = [d.isoformat() for d in days]
    totals = {u: sum(daily.get(u, {}).get(k, 0) for k in day_keys) for u in members}
    rows = sorted(members, key=lambda u: (-totals[u], u.lower()))

    n_rows, n_cols = len(rows), len(days)
    fig, ax = plt.subplots(figsize=(2.4 + 0.95 * n_cols, 1.5 + 0.34 * n_rows), dpi=160)
    fig.patch.set_facecolor(_CHART_SURFACE)
    ax.set_facecolor(_CHART_SURFACE)

    for r, user in enumerate(rows):
        for c, key in enumerate(day_keys):
            n = daily.get(user, {}).get(key, 0)
            fill, text = _chart_bin(n)
            # 0.92 cell leaves a surface-coloured gap between neighbours.
            ax.add_patch(Rectangle((c + 0.04, r + 0.04), 0.92, 0.92, color=fill, lw=0))
            if n:
                ax.text(c + 0.5, r + 0.5, str(n), ha="center", va="center",
                        fontsize=8, color=text)
        ax.text(n_cols + 0.25, r + 0.5, str(totals[user]), ha="left", va="center",
                fontsize=8, color=_CHART_INK if totals[user] else _CHART_MUTED)

    ax.set_xlim(0, n_cols + 0.9)
    ax.set_ylim(n_rows, 0)
    ax.set_xticks([c + 0.5 for c in range(n_cols)] + [n_cols + 0.45])
    ax.set_xticklabels([d.strftime("%a\n%d %b") for d in days] + ["Total"],
                       fontsize=8, color=_CHART_MUTED)
    ax.xaxis.tick_top()
    ax.set_yticks([r + 0.5 for r in range(n_rows)])
    ax.set_yticklabels(rows, fontsize=8, color=_CHART_INK)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.suptitle(f"Commits per day, last {n_cols} days (all branches)",
                 x=0.02, ha="left", fontsize=10, color=_CHART_INK, fontweight="bold")
    legend = [Patch(color=_CHART_ZERO, label="0")] + [
        Patch(color=f, label=label) for _, f, _, label in _CHART_BINS
    ]
    fig.legend(handles=legend, loc="lower left", bbox_to_anchor=(0.01, 0.0),
               ncol=len(legend), frameon=False, fontsize=7, labelcolor=_CHART_MUTED,
               handlelength=1.2, handleheight=1.0, columnspacing=1.2)
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_CHART_SURFACE)
    plt.close(fig)
    return buf.getvalue()


def _apply_skip_credits(state_user, skip_dates_iso):
    """
    Increment total_skips once per calendar day with no commits, only the first time we credit it.
    """
    credited = set(state_user.get("skip_credited_dates") or [])
    added = 0
    for d in sorted(skip_dates_iso):
        if d not in credited:
            credited.add(d)
            added += 1
    state_user["skip_credited_dates"] = sorted(credited)
    state_user["total_skips"] = state_user.get("total_skips", 0) + added
    return added


def run_monitor():
    members = get_team()
    state = load_data("state.json", {})

    start_d, end_d, date_range = commit_window_bounds()
    window_dates = [
        (start_d + timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS)
    ]
    logger.info("Orgs: %s", ", ".join(ORGS))
    logger.info("Window (01:00 IST days): %s (exclusive of today, %s days)", date_range, WINDOW_DAYS)
    days_by_user, meta_by_user = fetch_team_commit_days_all_branches(
        members, start_d, end_d
    )
    if members:
        fm = meta_by_user[members[0]]
        logger.info(
            "Repo pass: repos_scanned=%s, repos_skipped_stale_push=%s, branch_requests=%s",
            fm["repos_scanned"],
            fm["repos_skipped_stale_push"],
            fm["branch_requests"],
        )
    for user in members:
        days_with_commits = days_by_user[user]
        fetch_meta = meta_by_user[user]

        if user not in state:
            state[user] = {"total_skips": 0, "skip_credited_dates": []}
        else:
            su = state[user]
            su.setdefault("skip_credited_dates", [])
            su.setdefault("total_skips", 0)

        skip_dates = [d for d in window_dates if d not in days_with_commits]
        window_skip_days = len(skip_dates)
        _apply_skip_credits(state[user], skip_dates)

        state[user]["fetch_meta"] = fetch_meta
        state[user]["commit_breakdown"] = fetch_meta.get("commit_breakdown") or {}
        state[user]["window_skip_days"] = window_skip_days
        state[user]["window_dates"] = window_dates
        state[user]["days_with_commits_in_window"] = sorted(days_with_commits)
        if days_with_commits:
            newest = max(days_with_commits)
            if newest > (state[user].get("last_commit_date") or ""):
                state[user]["last_commit_date"] = newest

    last_day = end_d - timedelta(days=1)
    if start_d == last_day:
        checked = last_day.strftime("%a %d %b %Y")
    else:
        checked = f"{start_d.strftime('%d %b')} – {last_day.strftime('%d %b %Y')}"
    lines = [
        f"**Commit check for {checked}** (01:00 IST – 01:00 IST)",
        "",
        "**Active**",
        "",
    ]
    active = []
    for user in members:
        bd = meta_by_user[user].get("commit_breakdown") or {}
        total = meta_by_user[user]["unique_commits_in_window"]
        if total > 0:
            active.append((user, total, bd))
    active.sort(key=lambda x: (-x[1], x[0]))
    if not active:
        lines.extend(["(none)", ""])
    else:
        for user, total, bd in active:
            lines.append(f"{_display_name(user)} — {_plural(total, 'commit')}")
            for repo, branch, n in _breakdown_rows_sorted_by_commit_count(bd):
                repo_name = repo if len(ORGS) > 1 else repo.split("/", 1)[-1]
                lines.append(f"- {repo_name} @ `{branch}`: {n}")
            lines.append("")

    headers = _api_headers()
    # Runs for everyone: feeds both the heatmap and the inactive list below.
    newest, daily = _scan_lookback(headers, members, end_d, LOOKBACK_DAYS)
    chart_days = [end_d - timedelta(days=LOOKBACK_DAYS - i) for i in range(LOOKBACK_DAYS)]

    lines.append("**Developers who need to commit more:**")
    inactive = sorted(
        (u for u in members if meta_by_user[u]["unique_commits_in_window"] == 0),
        key=str.lower,
    )
    if not inactive:
        lines.append("(none)")
    else:
        today = _today_work_day()
        never = []
        logger.info("Searching default branches for %d inactive members", len(inactive))
        for user in inactive:
            logger.info("search last commit: %s", user)
            candidates = [
                i for i in (
                    newest.get(user),
                    _search_last_commit(headers, user),
                    _state_commit_info(state[user]),
                ) if i
            ]
            # Newest day wins; on a tie prefer the entry that knows its branch.
            last = (
                max(candidates, key=lambda i: (i["date"], bool(i.get("branch"))))
                if candidates else None
            )
            if not last:
                never.append(user)
                continue
            state[user]["last_commit_date"] = last["date"].isoformat()
            state[user]["last_commit"] = {
                "date": last["date"].isoformat(),
                "repo": last.get("repo"),
                "branch": last.get("branch"),
                "message": last.get("message"),
                "url": last.get("url"),
            }
            lines.append(_last_commit_line(user, last, today))
        if never:
            lines.append("")
            lines.append(
                "**No commits found:** " + ", ".join(_display_name(u) for u in never)
            )

    with open("state.json", "w") as f:
        json.dump(state, f, indent=4)

    chart = build_activity_chart(members, daily, chart_days)
    if chart:
        with open("activity.png", "wb") as f:
            f.write(chart)

    return "\n".join(lines), chart


def post_to_discord(report, url, chart=None):
    """Post the report to a Discord webhook, split under Discord's 2000-char cap,
    then the activity chart as an image attachment."""
    chunks, current = [], ""
    for line in report.strip().splitlines():
        if current and len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        if i:
            time.sleep(1)
        res = requests.post(
            url,
            json={"username": "Webuddhist commit checker", "content": chunk},
            timeout=30,
        )
        res.raise_for_status()
    logger.info("Posted report to Discord in %d message(s)", len(chunks))

    if chart:
        time.sleep(1)
        res = requests.post(
            url,
            data={"payload_json": json.dumps({"username": "Webuddhist commit checker"})},
            files={"files[0]": ("activity.png", chart, "image/png")},
            timeout=60,
        )
        res.raise_for_status()
        logger.info("Posted activity chart to Discord")


if __name__ == "__main__":
    _lvl = os.environ.get("MONITOR_LOG_LEVEL", "INFO").upper()
    _log_file = Path(
        os.environ.get("MONITOR_LOG_FILE", Path(__file__).resolve().parent / "monitor.log")
    )
    _log_file.parent.mkdir(parents=True, exist_ok=True)
    _handler = logging.FileHandler(_log_file, encoding="utf-8")
    _fmt = logging.Formatter(
        fmt="%(asctime)s UTC | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _fmt.converter = time.gmtime
    _handler.setFormatter(_fmt)
    # Logs also go to stderr so Render's log view shows them;
    # stdout stays the report alone.
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setFormatter(_fmt)
    logging.basicConfig(
        level=getattr(logging, _lvl, logging.INFO),
        handlers=[_handler, _stderr],
        force=True,
    )
    report, chart = run_monitor()
    print(report, flush=True)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook:
        post_to_discord(report, webhook, chart)
    else:
        logger.info("DISCORD_WEBHOOK_URL not set; report not posted")
