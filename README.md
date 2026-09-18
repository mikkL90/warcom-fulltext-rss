# Warhammer Community – full-text RSS

Generates a full-text RSS feed (article body + images inline, not just a
summary) for warhammer-community.com, for personal use in an RSS reader
like Reeder.

It discovers "what's new" directly from the Warhammer Community homepage
(an earlier version relied on the third-party warcomfeed.link, which
quietly stopped updating — this version has no third-party dependency in
the discovery path), then fetches each article page itself and pulls out
the title, date, hero image and full body content. Runs on stdlib Python
only — no dependencies to install.

## Setup (free, ~5 minutes)

1. Create a new **public** GitHub repo and push this folder to it.
2. In the repo settings → **Pages**, set source to "Deploy from a branch",
   branch `main`, folder `/docs`. GitHub will give you a URL like
   `https://<you>.github.io/<repo>/feed.xml`.
3. Edit `build_feed.py` and set `FEED_SELF_URL` to that exact URL (cosmetic
   only, but nice to have correct).
4. In the repo settings → **Actions → General**, under "Workflow
   permissions" pick **Read and write permissions** (needed so the
   scheduled job can commit the updated feed back to the repo).
5. Run the workflow once manually: **Actions → Update full-text RSS feed →
   Run workflow**. After it finishes, `docs/feed.xml` will be populated.
6. Add `https://<you>.github.io/<repo>/feed.xml` as a feed in Reeder.

After that, GitHub Actions refreshes the feed once a day (07:00 UTC) on
its own, for free (public repos get unlimited free Actions minutes).

## Notes

- `cache.json` stores already-scraped article bodies so re-runs only fetch
  *new* articles instead of re-scraping everything.
- The feed keeps the latest 60 articles.
- This is an unofficial, personal-use tool. All article content and images
  belong to Games Workshop / Warhammer Community.
