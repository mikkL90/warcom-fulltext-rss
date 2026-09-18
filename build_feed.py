#!/usr/bin/env python3
"""
Builds a full-text RSS feed for Warhammer Community.

"What's new" is discovered directly from the Warhammer Community homepage
(no third-party feed in the loop - a previous version relied on
warcomfeed.link, which quietly stopped updating). For each article we
don't already have cached, we fetch the real article page and pull out
the title, publish date, hero image and full body, then emit an RSS 2.0
feed with <content:encoded> so readers like Reeder show the complete
article instead of just a summary.

No third-party dependencies: stdlib only (urllib + re), so it runs anywhere,
including a bare GitHub Actions runner.
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
import xml.sax.saxutils as sax
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

HOME_URL = "https://www.warhammer-community.com/en-gb/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CACHE_PATH = Path(__file__).parent / "cache.json"
OUTPUT_PATH = Path(__file__).parent / "docs" / "feed.xml"
MAX_ITEMS = 60
DISCOVER_LIMIT = 30  # how many latest links to look at on the homepage each run
REQUEST_DELAY_SECONDS = 1.5  # be polite to the origin site
FEED_TITLE = "Warhammer Community (Full Text)"
FEED_SELF_URL = "https://mikkl90.github.io/warcom-fulltext-rss/feed.xml"
FEED_HOME_URL = "https://www.warhammer-community.com/"
FEED_DESCRIPTION = "Unofficial full-text mirror of Warhammer Community news, generated for personal RSS reading."

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
ARTICLE_LINK_RE = re.compile(r'href="(/en-gb/articles/[a-zA-Z0-9]+/[^"?#]+/)"')
DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{2,4})\b")


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def discover_latest_articles(limit: int = DISCOVER_LIMIT):
    """Return absolute article URLs in the order they first appear on the homepage."""
    html = fetch(HOME_URL).decode("utf-8", errors="replace")
    seen = []
    for m in ARTICLE_LINK_RE.finditer(html):
        url = "https://www.warhammer-community.com" + m.group(1)
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def parse_article_date(html: str):
    """The article's own publish date is the first <time> element on the page."""
    m = re.search(r"<time[^>]*>([^<]*)</time>", html)
    if not m:
        return None
    dm = DATE_RE.search(m.group(1))
    if not dm:
        return None
    day, month_word, year = dm.groups()
    month = MONTHS.get(month_word[:3].lower())
    if not month:
        return None
    year = int(year)
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_balanced_div(html: str, class_name: str):
    """Return the inner HTML of the first <div> whose class list contains class_name."""
    m = re.search(r'<div[^>]*class="[^"]*\b' + re.escape(class_name) + r'\b[^"]*"[^>]*>', html)
    if not m:
        return None
    pos = m.end()
    depth = 1
    tag_re = re.compile(r'<div\b|</div\s*>')
    while depth > 0:
        m2 = tag_re.search(html, pos)
        if not m2:
            return None
        depth += 1 if m2.group(0).startswith("<div") else -1
        pos = m2.end()
    return html[m.end():m2.start()]


def clean_article_html(fragment: str) -> str:
    # Drop share widgets / scripts / noscript that sometimes ride along, keep text+images+headings+lists
    fragment = re.sub(r"<script\b.*?</script>", "", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<noscript\b.*?</noscript>", "", fragment, flags=re.S | re.I)
    # Strip Next.js image loading/style cruft attributes to keep it lean (keep src/alt/width/height)
    fragment = re.sub(r'\s(loading|decoding|data-nimg|style)="[^"]*"', "", fragment)
    return fragment.strip()


def scrape_article(url: str):
    """Fetch one article page and return its title/date/hero/description/content, or None on failure."""
    page_url = url if url.endswith("/") else url + "/"
    try:
        html = fetch(page_url).decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"  ! failed to fetch {page_url}: {e}", file=sys.stderr)
        return None

    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else None

    desc_m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
    description = desc_m.group(1) if desc_m else ""

    hero_url = None
    hero_section_m = re.search(r'<section[^>]*class="[^"]*\barticle-hero\b[^"]*"[^>]*>(.*?)</section>', html, re.S)
    if hero_section_m:
        img_m = re.search(r'<img[^>]+src="([^"]+)"', hero_section_m.group(1))
        if img_m:
            hero_url = img_m.group(1)

    content = extract_balanced_div(html, "article-content")
    if content is None:
        return None
    content = clean_article_html(content)

    pub_date = parse_article_date(html)

    return {
        "title": title,
        "description": description,
        "hero": hero_url,
        "content": content,
        "pub_date": pub_date,
    }


def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict):
    CACHE_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False))


def build_rss(entries: list) -> str:
    now = format_datetime(datetime.now(timezone.utc))
    items_xml = []
    for e in entries:
        img_block = f'<enclosure url="{sax.escape(e["hero"])}" type="image/jpeg"/>' if e.get("hero") else ""
        items_xml.append(f"""
  <item>
    <title>{sax.escape(e['title'] or '')}</title>
    <link>{sax.escape(e['link'])}</link>
    <guid isPermaLink="true">{sax.escape(e['link'])}</guid>
    <pubDate>{sax.escape(e['pub_date_str'])}</pubDate>
    {img_block}
    <description>{sax.escape(e.get('description') or '')}</description>
    <content:encoded><![CDATA[{e['content_html']}]]></content:encoded>
  </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{sax.escape(FEED_TITLE)}</title>
  <link>{sax.escape(FEED_HOME_URL)}</link>
  <atom:link href="{sax.escape(FEED_SELF_URL)}" rel="self" type="application/rss+xml"/>
  <description>{sax.escape(FEED_DESCRIPTION)}</description>
  <language>en-gb</language>
  <lastBuildDate>{now}</lastBuildDate>
  {''.join(items_xml)}
</channel>
</rss>
"""


def main():
    print("Discovering latest articles from:", HOME_URL)
    links = discover_latest_articles()
    print(f"Found {len(links)} candidate article links.")
    cache = load_cache()

    entries = []
    new_count = 0
    for link in links:
        cached = cache.get(link)
        if cached:
            entry = dict(cached)
        else:
            print("Scraping new article:", link)
            scraped = scrape_article(link)
            new_count += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            if scraped is None:
                print(f"  ! could not extract content, skipping {link}", file=sys.stderr)
                continue
            entry = {
                "title": scraped["title"],
                "description": scraped["description"],
                "hero": scraped["hero"],
                "content_html": scraped["content"],
                "pub_date_str": format_datetime(scraped["pub_date"]) if scraped["pub_date"] else format_datetime(datetime.now(timezone.utc)),
            }
            cache[link] = entry

        entries.append({**entry, "link": link})

    # newest first, using each article's own scraped publish date
    entries.sort(key=lambda e: parsedate_to_datetime(e["pub_date_str"]), reverse=True)
    entries = entries[:MAX_ITEMS]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_rss(entries), encoding="utf-8")
    # rebuild the cache from what's actually in the feed, so stale/renamed keys don't pile up
    save_cache({e["link"]: {k: v for k, v in e.items() if k != "link"} for e in entries})
    print(f"Wrote {OUTPUT_PATH} with {len(entries)} items ({new_count} newly scraped).")


if __name__ == "__main__":
    main()
