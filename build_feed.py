#!/usr/bin/env python3
"""
Builds a full-text RSS feed for Warhammer Community.

Source of truth for "what's new" is the unofficial warcomfeed.link RSS feed
(title/link/date/category). For each article we don't already have cached,
we fetch the real article page and pull out the full body + images, then
emit an RSS 2.0 feed with <content:encoded> so readers like Reeder show the
complete article instead of just a summary.

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
from email.utils import format_datetime

SOURCE_FEED = "https://warcomfeed.link/rss.xml"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CACHE_PATH = Path(__file__).parent / "cache.json"
OUTPUT_PATH = Path(__file__).parent / "docs" / "feed.xml"
MAX_ITEMS = 60
REQUEST_DELAY_SECONDS = 1.5  # be polite to the origin site
FEED_TITLE = "Warhammer Community (Full Text)"
FEED_SELF_URL = "https://mikkl90.github.io/warcom-fulltext-rss/feed.xml"
FEED_HOME_URL = "https://www.warhammer-community.com/"
FEED_DESCRIPTION = "Unofficial full-text mirror of Warhammer Community news, generated for personal RSS reading."


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    """Fetch one article page and return (title, hero_image_url, content_html) or None on failure."""
    page_url = url if url.endswith("/") else url + "/"
    try:
        html = fetch(page_url).decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"  ! failed to fetch {page_url}: {e}", file=sys.stderr)
        return None

    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else None

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

    return {"title": title, "hero": hero_url, "content": content}


def parse_source_feed(xml_text: str):
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S):
        def field(tag):
            m = re.search(rf"<{tag}[^>]*>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</{tag}>", block, re.S)
            if not m:
                return ""
            return (m.group(1) or m.group(2) or "").strip()

        link = field("link")
        if not link:
            continue
        enclosure_m = re.search(r'<enclosure[^>]+url="([^"]+)"', block)
        items.append({
            "title": field("title"),
            "link": link,
            "guid": field("guid") or link,
            "description": field("description"),
            "pubDate": field("pubDate"),
            "category": field("category"),
            "enclosure": enclosure_m.group(1) if enclosure_m else None,
        })
    return items


def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict):
    # keep the cache file bounded to what the feed actually needs
    trimmed = dict(list(cache.items())[:MAX_ITEMS])
    CACHE_PATH.write_text(json.dumps(trimmed, indent=1, ensure_ascii=False))


def build_rss(entries: list) -> str:
    now = format_datetime(datetime.now(timezone.utc))
    items_xml = []
    for e in entries:
        img_block = f'<enclosure url="{sax.escape(e["hero"])}" type="image/jpeg"/>' if e.get("hero") else ""
        items_xml.append(f"""
  <item>
    <title>{sax.escape(e['title'] or '')}</title>
    <link>{sax.escape(e['link'])}</link>
    <guid isPermaLink="true">{sax.escape(e['guid'])}</guid>
    <pubDate>{sax.escape(e['pubDate'] or '')}</pubDate>
    <category>{sax.escape(e.get('category') or '')}</category>
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
    print("Fetching source feed:", SOURCE_FEED)
    source_items = parse_source_feed(fetch(SOURCE_FEED).decode("utf-8", errors="replace"))[:MAX_ITEMS]
    cache = load_cache()

    entries = []
    new_count = 0
    for item in source_items:
        cached = cache.get(item["guid"])
        if cached:
            content_html = cached["content_html"]
            hero = cached.get("hero") or item["enclosure"]
        else:
            print("Scraping new article:", item["link"])
            scraped = scrape_article(item["link"])
            new_count += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            if scraped is None:
                # fall back to the summary-only description so the item isn't dropped
                content_html = f"<p>{sax.escape(item['description'])}</p>"
                hero = item["enclosure"]
            else:
                content_html = scraped["content"]
                hero = scraped["hero"] or item["enclosure"]
            cache[item["guid"]] = {"content_html": content_html, "hero": hero}

        entries.append({**item, "content_html": content_html, "hero": hero})

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_rss(entries), encoding="utf-8")
    save_cache(cache)
    print(f"Wrote {OUTPUT_PATH} with {len(entries)} items ({new_count} newly scraped).")


if __name__ == "__main__":
    main()
