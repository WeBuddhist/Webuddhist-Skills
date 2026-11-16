#!/usr/bin/env python3
"""Turn a collected profiles.json into Buddhist creator ranking files.

Usage: python3 build_ranking.py profiles.json OUT_DIR [--top 100] [--previous old/ranking.json]
Writes OUT_DIR/{combined,youtube,instagram,tiktok,left_out}.csv and OUT_DIR/ranking.json.
"""
import argparse, csv, json, re, sys
from datetime import date
from pathlib import Path

PLATFORMS = ["youtube", "instagram", "tiktok"]
TYPES = {"person", "organisation"}
UNITS = {"k": 1e3, "m": 1e6, "b": 1e9, "l": 1e5, "lakh": 1e5, "lac": 1e5, "cr": 1e7, "crore": 1e7}
STALE_DAYS = 180


def parse_count(value):
    """'1.2M', '354.7K', '1,234,567', '21.7L', '12 lakh' -> int. None if unknown."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*(k|m|b|lakh|lac|l|crore|cr)?\+?", text)
    if not m:
        return None
    return int(round(float(m.group(1)) * UNITS.get(m.group(2), 1)))


def is_stale(checked, today):
    """True when a count is undated or older than STALE_DAYS."""
    try:
        parts = [int(x) for x in str(checked).split("-")] + [1, 1]
        return (today - date(*parts[:3])).days > STALE_DAYS
    except (ValueError, TypeError):
        return True


def key_of(creator):
    """Stable identity for matching a creator between two runs."""
    for p in PLATFORMS:
        handle = (creator["accounts"].get(p) or {}).get("handle")
        if handle:
            return f"{p}:{handle.lower().lstrip('@')}"
    return "name:" + creator["name"].lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profiles")
    ap.add_argument("out_dir")
    ap.add_argument("--top", type=int, default=100, help="roster size, cut by combined followers")
    ap.add_argument("--previous", help="last run's ranking.json, to show movement")
    args = ap.parse_args()

    data = json.loads(Path(args.profiles).read_text(encoding="utf-8"))
    today = date.fromisoformat(data["collected_on"])
    warnings, creators, seen = [], [], {}
    for raw in data["creators"]:
        c = {k: raw.get(k, "") for k in ("name", "type", "country", "tradition", "language", "note")}
        c["accounts"] = {}
        if c["type"] not in TYPES:
            warnings.append(f"{c['name']}: type '{c['type']}' is not person/organisation")
        for p in PLATFORMS:
            acc = (raw.get("accounts") or {}).get(p)
            if not acc:
                continue
            n = parse_count(acc.get("followers"))
            if n is None:
                warnings.append(f"{c['name']}: {p} count '{acc.get('followers')}' unreadable, left out")
                continue
            handle = (acc.get("handle") or "").strip()
            if handle:
                dup = seen.setdefault(f"{p}:{handle.lower().lstrip('@')}", c["name"])
                if dup != c["name"]:
                    warnings.append(f"{p} {handle} appears under both '{dup}' and '{c['name']}'")
            if not acc.get("source"):
                warnings.append(f"{c['name']}: {p} count has no source")
            c["accounts"][p] = {"handle": handle, "url": acc.get("url", ""), "followers": n,
                                "source": acc.get("source", ""), "checked": acc.get("checked", ""),
                                "stale": is_stale(acc.get("checked"), today)}
        if not c["accounts"]:
            warnings.append(f"{c['name']}: no usable follower count on any platform, dropped")
            continue
        c["total"] = sum(a["followers"] for a in c["accounts"].values())
        creators.append(c)

    creators.sort(key=lambda c: (-c["total"], c["name"]))
    pool_size = len(creators)
    creators = creators[: args.top]

    prev = {}
    if args.previous:
        old = json.loads(Path(args.previous).read_text(encoding="utf-8"))
        for board, rows in old.get("boards", {}).items():
            for row in rows:
                prev[(board, row["key"])] = row["rank"]

    def board(name, value):
        rows = [c for c in creators if value(c) is not None]
        rows.sort(key=lambda c: (-value(c), c["name"]))
        out = []
        for i, c in enumerate(rows, 1):
            k = key_of(c)
            was = prev.get((name, k))
            row = {"rank": i, "previous_rank": was if was else "",
                   "change": ("new" if args.previous else "") if was is None else was - i,
                   "name": c["name"], "type": c["type"], "country": c["country"],
                   "tradition": c["tradition"], "language": c["language"], "key": k}
            for p in PLATFORMS:
                a = c["accounts"].get(p, {})
                row[f"{p}_handle"] = a.get("handle", "")
                row[f"{p}_followers"] = a.get("followers", "")
            row["total_followers"] = c["total"]
            row["platforms"] = len(c["accounts"])
            a = c["accounts"].get(name, {})
            row["source"] = a.get("source", "") if name != "combined" else "; ".join(
                x["source"] for x in c["accounts"].values() if x["source"])
            row["checked"] = a.get("checked", "") if name != "combined" else max(
                (x["checked"] for x in c["accounts"].values()), default="")
            used = c["accounts"].values() if name == "combined" else [a]
            row["stale"] = "yes" if any(x.get("stale") for x in used) else ""
            row["note"] = c["note"]
            out.append(row)
        return out

    boards = {"combined": board("combined", lambda c: c["total"])}
    for p in PLATFORMS:
        boards[p] = board(p, lambda c, p=p: (c["accounts"].get(p) or {}).get("followers"))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in boards.items():
        with open(out / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as f:
            cols = [k for k in (rows[0] if rows else {"rank": 0}) if k != "key"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    left_out = data.get("left_out", [])
    with open(out / "left_out.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["name", "platform", "handle", "followers", "reason"], extrasaction="ignore")
        w.writeheader()
        w.writerows(left_out)
    stale = sum(1 for r in boards["combined"] if r["stale"])
    result = {
        "collected_on": data.get("collected_on", ""),
        "method": "Public follower/subscriber counts on YouTube, Instagram and TikTok. "
                  "Combined = sum of the counts found; audiences overlap, so it measures reach, not unique people.",
        "roster_size": len(creators), "pool_size": pool_size,
        "stale_rows": stale, "boards": boards, "left_out": left_out, "warnings": warnings,
    }
    (out / "ranking.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Roster: {len(creators)} of {pool_size} collected (cut at --top {args.top})")
    for name, rows in boards.items():
        top = f"#1 {rows[0]['name']}" if rows else "empty"
        print(f"  {name}: {len(rows)} rows, {top}")
    print(f"Stale or undated counts (>{STALE_DAYS} days): {stale} creators. Left out: {len(left_out)}")
    print(f"Warnings: {len(warnings)}")
    for w_ in warnings:
        print("  -", w_)


if __name__ == "__main__":
    sys.exit(main())
