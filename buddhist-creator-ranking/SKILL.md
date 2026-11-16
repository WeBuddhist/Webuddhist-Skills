---
name: buddhist-creator-ranking
description: Collect the top Buddhist creators (people and organisations) on YouTube, Instagram and TikTok, look up their public follower counts without paid APIs, and build ranked leaderboards as CSV and JSON files. Use this whenever the user asks for a Buddhist creator or influencer ranking, a top-100 list, a leaderboard for Buddhist Creators / the Buddhist Creators Convention, a weekly refresh of an existing ranking, or follower counts for a list of Buddhist teachers, monastics, channels or centres, even if they don't say "ranking".
profile: any
updated: 2026-11-16
---

# Buddhist creator ranking

Build a ranked list of Buddhist creators from public follower counts, with a source and date behind every number. The output is a folder of files: one leaderboard per platform, a combined one, and a list of what was left out and why.

Defaults, unless the user says otherwise: about 100 creators, worldwide, people and organisations (labelled), platforms YouTube, Instagram and TikTok.

If the user hands you names or handles (for example the convention's creator list), skip discovery and go straight to looking up counts. If they give you a previous `ranking.json`, this is a refresh: see "Refreshing".

## How the work goes

1. **Discover** candidates, more than you need (aim for 130% of the target, so the cut-off is earned).
2. **Classify** each one: person, organisation, or left out.
3. **Look up counts** per platform, recording where and when each number comes from.
4. **Build** the files with `scripts/build_ranking.py`, which ships in this folder.
5. **Report** honestly what the ranking covers and where it is thin.

Write everything into one `profiles.json` as you go (format below). Keep notes in the file, not in your head: a run touches a hundred accounts.

## 1. Discover

Ordinary web searches return long, noisy pages. Two things work much better:

- **List pages**, fetched with a tight instruction such as "one line per account: name | handle or URL | followers, no commentary". Good starting points: Feedspot's Buddhist YouTube channel list, HypeAuditor's Instagram category pages for "buddhism" and "buddha" (different language versions of the page show different slices), Favikon and Heepsy topic lists, and news features about monks and teachers on TikTok.
- **Regional sweeps.** The largest Buddhist audiences are not English-speaking, and English list pages are dominated by small Western centres. A ranking built only from them puts a 400-subscriber temple in the top 100 while missing teachers with millions of followers. Search in the local language and script for each of: Vietnamese, Thai, Sinhala, Chinese (Taiwan and the diaspora), Hindi and Marathi (including Ambedkarite Buddhist creators), Korean, Japanese, Burmese, Khmer, Tibetan and Nepali. Adding "vidiq" or "socialblade" to a query tends to surface stat pages with numbers already in the snippet.

Take handles from what you find. Do not guess a handle from a name unless you can see the account's display name in what comes back: a wrong guess can silently return someone else's numbers.

## 2. Classify

Give every candidate a `type`:

- `person`: an individual teacher, monastic or lay creator, including their official channel even if a team runs it.
- `organisation`: monasteries, centres, publishers, TV channels and media brands. A faceless channel that makes its own Buddhist content belongs here with the note "faceless channel". A fan-run channel that re-uploads one teacher's talks also goes here, with a note naming the teacher; never add its numbers to the teacher's own total. A creator gets one account per platform: when a teacher or organisation has several official channels, put the largest on their row and list the others as separate organisation rows with a note.

Leave out, and record in `left_out` with a reason:

- anonymous quote, meme and fan pages that repost rather than create;
- AI-generated personas;
- channels that only use "Buddha" in the name (satire, general spirituality, motivation from other traditions);
- accounts you could not confirm are mainly Buddhist content. Category pages on influencer sites include many personal accounts that merely mention Buddhism. Mark these "needs review" rather than ranking or silently dropping them, so the user can overrule you.

Fill `country`, `tradition` and `language` only when the source states them or you are sure. Blank is better than a guess.

Use public account information only. List sites often show emails and phone numbers; do not copy them.

## 3. Look up counts

What works without a browser, by platform:

- **YouTube.** The channel page itself shows no subscriber count when fetched. Use `https://vidiq.com/youtube-stats/channel/@HANDLE/`, which gives subscribers and an "as of" date. The same site's channel-ID URLs often come back without numbers; they do reveal the handle, so retry with the handle form. Social Blade's `socialblade.com/youtube/handle/HANDLE` works as a second source.
- **Instagram.** Profile pages cannot be fetched. Use `https://socialveins.com/influencer/instagram/HANDLE` (sometimes errors; retry once) and the HypeAuditor category pages. Indian sites write counts in lakh ("21.7L"); record them as printed.
- **TikTok.** Profile pages and most stat sites cannot be fetched. Counts come from the occasional creator-database page and from dated press articles, so this is the thinnest board.
- **A browser beats all of these.** If a browser tool is available in the session, use it for all three platforms (see "With a browser" below). In testing it turned a 79-creator, English-heavy run into a full 100 with no stale counts. Without one, tell the user at the start that Instagram and TikTok will be thin.

For every count record the number as printed (`"1.2M"`, `"354.7K"`, `"21.7L"`; the script converts), the `source` URL, and `checked`: the date the number was true. Use the date the source prints; for a live stats page or profile that prints none, use today's date; for an article or list page with no date, leave it blank. The script flags blank or older-than-180-days counts as stale, which is how the user sees which numbers to trust.

When two sources disagree, keep the more recently dated one, and prefer the platform itself over any third party. Check everyone likely to land in the top quarter against a live source; the long tail can keep list-page numbers.

If you cannot find a count for a platform, omit that platform. Never estimate.

### With a browser

Read the numbers from the platforms themselves. Every profile is a public page, and fetching several from one script running on the site's own page keeps the tool calls few. Go gently (about a second between requests, a few dozen profiles per call) and keep each script call under roughly 40 seconds or it times out; store results on `window` so a timed-out call can still be read back.

- **YouTube: discovery and counts in one step.** From any youtube.com page, fetch `/results?search_query=QUERY&sp=EgIQAg%253D%253D&hl=en` (the channel filter), parse `ytInitialData`, and collect every `channelRenderer`: title, handle (`canonicalBaseUrl`), description snippet, and the text containing "subscribers". Run this for native-script queries per region ("Phật giáo", "pháp thoại", "ธรรมะ", "불교", "스님 법문", "仏教", "佛教", "बुद्ध की कहानी", "බණ", "တရားတော်များ", "ព្រះធម៌", plus English terms), keep channels above a floor such as 100K, and you have most of the YouTube board with today's numbers. For a single channel, parse `ytInitialData.header` on its page. Do not grab the first "N subscribers" string in a page's HTML: it is often another channel's.
- **Instagram.** From an instagram.com page, fetch `/HANDLE/` and read the `og:description` meta tag: "139K Followers, ... from Name (@handle)". The name confirms you have the right account; no such tag means the handle does not exist. The tag rounds hard above a million ("2M" for 1.67M), so for the biggest accounts open the profile and read the exact figure from the follower count's `title` attribute.
- **TikTok.** Try the profile first. TikTok is blocked in some countries, India among them; if it does not load, open `socialblade.com/tiktok/user/HANDLE` in the browser instead, which shows display name and followers there even though it cannot be fetched without a browser.
- **Finding a creator's other platforms.** Look for Instagram and TikTok links on the YouTube channel page, then try the YouTube handle on the other two. Accept a match only when the display name fits and the audience is plausible: big names often have tiny look-alike or fan accounts under the obvious handle. Third-party list sites were wrong often enough in testing (one account listed at 137K had 0 followers; another was off by a factor of three) that anything they report should be re-read from the profile.
- **Say what the browser could not reach.** Instagram's own search needs a sign-in and TikTok may be blocked, so creators who are big only on those two are still found through list pages and press. Name that gap in the report.

## 4. Build the files

`profiles.json`:

```json
{
  "collected_on": "2026-11-16",
  "creators": [
    {
      "name": "Example Rinpoche",
      "type": "person",
      "country": "Nepal",
      "tradition": "Tibetan",
      "language": "English",
      "note": "",
      "accounts": {
        "youtube":   {"handle": "@example", "followers": "753K", "source": "https://www.youtube.com/@example", "checked": "2026-11-16"},
        "instagram": {"handle": "@example", "followers": "1,671,127", "source": "https://www.instagram.com/example/", "checked": "2026-11-16"}
      }
    }
  ],
  "left_out": [
    {"name": "Daily Quotes", "platform": "instagram", "handle": "@dailyquotes", "followers": "290K", "reason": "anonymous quote page"}
  ]
}
```

Run the script that ships with this skill (standard library only, Python 3.8+):

```
python3 scripts/build_ranking.py profiles.json buddhist-creator-ranking-YYYY-MM-DD --top 100
```

It keeps the top N creators by combined followers and writes `combined.csv`, `youtube.csv`, `instagram.csv`, `tiktok.csv`, `left_out.csv` and `ranking.json` (everything, for loading into a site or database). Each platform board lists the roster members who have that platform. Fix every warning it prints (duplicate handles, unreadable counts, missing sources) and rerun before delivering.

Combined followers are a simple sum across platforms. Audiences overlap, so it measures reach, not unique people; say so if the user quotes it.

## 5. Report

Deliver the folder's files, then tell the user in a few lines: how many were ranked against the target, the top five, how many rows each board has, how many counts are stale, how many accounts are waiting in `left_out` for review, and which regions or platforms are thin. If you found fewer than the target, say so. Do not pad the list with tiny accounts or unverified ones to reach a round number: a ranking is only useful if the user can trust who is on it.

## Refreshing

When there is a previous `ranking.json`, start from its roster instead of rediscovering: re-check each saved handle at its saved source, then spend the remaining effort on newcomers and on the regions the last run called thin. Run the script with `--previous path/to/old/ranking.json` to fill the `previous_rank` and `change` columns (positive means moved up; "new" means not on the last board).
