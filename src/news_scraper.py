"""Scrapes AI news from RSS feeds, DuckDuckGo news, web search, and Gmail newsletters.

News freshness: all sources are filtered to last 7 days max.
Full article extraction: trafilatura pulls real article text from URLs so
Claude generates posts from actual content, not thin RSS snippets.
"""
import json
import asyncio
import feedparser
import requests
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from config import (
    AI_NEWS_FEEDS, RESEARCH_FEEDS, AI_SEARCH_QUERIES, POSTS_DIR, LOGS_DIR,
    GMAIL_SEARCH_QUERY, get_setting,
    get_news_feeds, get_search_queries, get_gmail_search_query,
)


def scrape_rss_feeds() -> list[dict]:
    """Pull latest news from the configured RSS feeds (user topic or AI default)."""
    articles = []
    for feed_url in get_news_feeds():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                published = entry.get("published", "")
                articles.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:500],
                    "link": entry.get("link", ""),
                    "source": feed.feed.get("title", feed_url),
                    "published": published,
                    "type": "rss"
                })
        except Exception as e:
            print(f"  [RSS Error] {feed_url}: {e}")
    return articles


def scrape_research_feeds() -> list[dict]:
    """Pull latest research papers + AI lab blog posts.
    Tagged with type='research' for the RESEARCH badge in UI."""
    if not get_setting("use_research_feeds", True):
        return []
    articles = []
    for feed_url in RESEARCH_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:4]:
                published = entry.get("published", "")
                # arXiv entries have author info worth surfacing
                authors = ""
                if hasattr(entry, "authors"):
                    authors = ", ".join(a.get("name", "") for a in entry.authors[:3])
                elif hasattr(entry, "author"):
                    authors = entry.author
                summary = entry.get("summary", "")[:600]
                if authors:
                    summary = f"By {authors}. {summary}"
                articles.append({
                    "title": entry.get("title", ""),
                    "summary": summary,
                    "link": entry.get("link", ""),
                    "source": feed.feed.get("title", feed_url),
                    "published": published,
                    "type": "research"
                })
        except Exception as e:
            print(f"  [Research Error] {feed_url}: {e}")
    return articles


def scrape_ddg_news() -> list[dict]:
    """Search DuckDuckGo News for fresh AI/tech articles (last 7 days).
    No API key needed. Reliable and fast."""
    articles = []
    try:
        import time as _time
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        queries = [
            "AI artificial intelligence news",
            "LLM AI agent developer tools",
            "cloud platform engineering AI",
        ]
        seen_urls = set()

        with DDGS() as ddgs:
            for qi, query in enumerate(queries):
                if qi > 0:
                    _time.sleep(3)  # Avoid DDG rate limits between queries
                try:
                    results = ddgs.news(
                        query,
                        region="wt-wt",       # worldwide
                        safesearch="moderate",
                        timelimit="w",         # past week
                        max_results=8,
                    )
                    for r in results:
                        url = r.get("url", "")
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        articles.append({
                            "title": r.get("title", ""),
                            "summary": r.get("body", "")[:500],
                            "link": url,
                            "source": r.get("source", "DuckDuckGo News"),
                            "published": r.get("date", datetime.now().isoformat()),
                            "type": "ddg_news",
                        })
                except Exception as e:
                    print(f"  [DDG News Error] {query}: {e}")
    except ImportError:
        print("  [DDG] duckduckgo-search not installed. Run: pip install duckduckgo-search")
    except Exception as e:
        print(f"  [DDG] Unexpected error: {e}")
    return articles


def fetch_full_article(url: str, timeout: int = 15) -> str:
    """Extract the full article text from a URL using trafilatura.
    Returns cleaned article body text (up to 3000 chars).
    Falls back to basic requests+BS4 if trafilatura fails."""
    if not url or not url.startswith("http"):
        return ""

    # Try trafilatura first (best quality extraction)
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            if text and len(text) > 100:
                return text[:3000]
    except Exception as e:
        print(f"  [Trafilatura] Error on {url[:60]}: {e}")

    # Fallback: requests + BeautifulSoup
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove nav, footer, script, style
        for tag in soup(["nav", "footer", "script", "style", "aside", "header"]):
            tag.decompose()

        # Try article tag first, then main, then body
        container = soup.find("article") or soup.find("main") or soup.find("body")
        if container:
            paragraphs = container.find_all("p")
            text = "\n\n".join(p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 30)
            if text:
                return text[:3000]
    except Exception as e:
        print(f"  [BS4 fallback] Error on {url[:60]}: {e}")

    return ""


async def scrape_web_search() -> list[dict]:
    """Use Playwright to search for latest AI news.
    NOTE: This is a fallback — prefer scrape_ddg_news() which is faster and
    doesn't need a browser."""
    articles = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            for query in get_search_queries()[:2]:
                try:
                    search_url = f"https://www.google.com/search?q={query}&tbm=nws"
                    await page.goto(search_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)

                    results = await page.query_selector_all("div.SoaBEf")
                    for result in results[:3]:
                        title_el = await result.query_selector("div.MBeuO")
                        snippet_el = await result.query_selector("div.GI74Re")
                        link_el = await result.query_selector("a")

                        title = await title_el.inner_text() if title_el else ""
                        snippet = await snippet_el.inner_text() if snippet_el else ""
                        link = await link_el.get_attribute("href") if link_el else ""

                        if title:
                            articles.append({
                                "title": title,
                                "summary": snippet,
                                "link": link,
                                "source": "Google News",
                                "published": datetime.now().isoformat(),
                                "type": "web_search"
                            })
                except Exception as e:
                    print(f"  [Search Error] {query}: {e}")

            await browser.close()
    except Exception as e:
        print(f"  [Playwright Search] Skipping: {e}")
    return articles


def scrape_gmail_newsletters(gmail_service=None) -> list[dict]:
    """
    Extract AI news from Gmail newsletters.
    Requires Gmail API credentials (setup via gmail_setup.py).
    Falls back gracefully if not configured.
    """
    articles = []
    creds_file = Path(__file__).resolve().parent.parent / "auth_state" / "gmail_token.json"

    if not creds_file.exists():
        print("  [Gmail] No credentials found. Run gmail_setup.py first.")
        print("  [Gmail] Falling back to RSS + web scraping only.")
        return articles

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(creds_file))
        service = build("gmail", "v1", credentials=creds)

        results = service.users().messages().list(
            userId="me",
            q=get_gmail_search_query(),
            maxResults=10
        ).execute()

        messages = results.get("messages", [])
        for msg_data in messages[:5]:
            msg = service.users().messages().get(
                userId="me", id=msg_data["id"], format="full"
            ).execute()

            headers = msg["payload"]["headers"]
            subject = next(
                (h["value"] for h in headers if h["name"] == "Subject"), ""
            )
            sender = next(
                (h["value"] for h in headers if h["name"] == "From"), ""
            )

            # Get body snippet
            snippet = msg.get("snippet", "")

            articles.append({
                "title": subject,
                "summary": snippet[:500],
                "link": f"https://mail.google.com/mail/u/0/#inbox/{msg_data['id']}",
                "source": f"Gmail: {sender}",
                "published": datetime.now().isoformat(),
                "type": "gmail"
            })
    except Exception as e:
        print(f"  [Gmail Error] {e}")

    return articles


def _is_fresh(published: str, max_days: int = 7) -> bool:
    """Check if a published date string is within the last max_days."""
    if not published:
        return True  # No date = assume fresh (benefit of the doubt)
    try:
        from dateutil.parser import parse as dateparse
        pub_dt = dateparse(published, fuzzy=True)
        # Make naive if aware
        if pub_dt.tzinfo:
            pub_dt = pub_dt.replace(tzinfo=None)
        return (datetime.now() - pub_dt).days <= max_days
    except Exception:
        return True  # Can't parse = keep it


async def gather_all_news() -> list[dict]:
    """Collect news from all sources, filter to last 7 days, deduplicate."""
    print("[Scraper] Gathering AI news...")

    # DuckDuckGo News first (most reliable fresh source, no browser needed)
    print("  Searching DuckDuckGo News (past week)...")
    ddg_articles = scrape_ddg_news()
    print(f"  Found {len(ddg_articles)} DDG news articles")

    print("  Checking RSS feeds...")
    rss_articles = scrape_rss_feeds()
    print(f"  Found {len(rss_articles)} RSS articles")

    print("  Checking research paper feeds...")
    research_articles = scrape_research_feeds()
    print(f"  Found {len(research_articles)} research articles")

    # Playwright web search as fallback (slower, can break)
    print("  Searching the web (Playwright)...")
    web_articles = await scrape_web_search()
    print(f"  Found {len(web_articles)} web articles")

    print("  Checking Gmail newsletters...")
    gmail_articles = scrape_gmail_newsletters()
    print(f"  Found {len(gmail_articles)} Gmail articles")

    # DDG first (freshest), then RSS, research, web, gmail
    all_articles = ddg_articles + rss_articles + research_articles + web_articles + gmail_articles

    # ─── Filter: only keep articles from the last 7 days ─────────────────
    fresh = [a for a in all_articles if _is_fresh(a.get("published", ""), max_days=7)]
    stale_count = len(all_articles) - len(fresh)
    if stale_count:
        print(f"  Filtered out {stale_count} stale articles (older than 7 days)")

    # Deduplicate by title similarity
    seen_titles = set()
    unique = []
    for article in fresh:
        title_key = article["title"].lower()[:50]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique.append(article)

    # Sort by relevance (prefer recent, prefer diverse sources)
    unique.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Save raw news data
    news_file = POSTS_DIR / f"news_raw_{datetime.now().strftime('%Y%m%d')}.json"
    with open(news_file, "w", encoding="utf-8") as f:
        json.dump(unique, f, indent=2, ensure_ascii=False)

    print(f"[Scraper] Total unique fresh articles: {len(unique)}")
    max_articles = get_setting("max_cards_per_day", 15) * 2
    return unique[:max_articles]


if __name__ == "__main__":
    articles = asyncio.run(gather_all_news())
    for i, a in enumerate(articles, 1):
        print(f"\n{i}. [{a['type']}] {a['title']}")
        print(f"   {a['summary'][:100]}...")
