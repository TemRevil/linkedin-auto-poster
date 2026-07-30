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
    AUTH_DIR, SESSIONS_FILE,
    GMAIL_SEARCH_QUERY, get_setting,
    get_news_feeds, get_search_queries, get_gmail_search_query,
)


def _get_active_gmail_session_file() -> Path:
    """Resolve the active Gmail session's token file from sessions.json,
    falling back to the legacy gmail_token.json so single-session installs
    keep working."""
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            for s in config.get("gmail", []):
                if s.get("active") and s.get("file"):
                    return AUTH_DIR / s["file"]
        except (json.JSONDecodeError, OSError):
            pass
    return AUTH_DIR / "gmail_token.json"


def _articles_from_feed(feed_url: str, limit: int, atype: str,
                        summary_len: int = 500, enrich=None) -> list[dict]:
    """Parse one feed and return up to ``limit`` article dicts tagged ``atype``.

    Shared by scrape_rss_feeds and scrape_research_feeds so both build articles
    the same way and both catch per-feed errors. ``enrich(entry, summary) ->
    summary`` optionally rewrites the (already truncated) summary — research
    feeds use it to prepend author names. ``entry.get("summary")`` is coerced
    with ``or ""`` so a feed that yields a None summary can't raise.
    """
    out = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:limit]:
            summary = (entry.get("summary") or "")[:summary_len]
            if enrich is not None:
                summary = enrich(entry, summary)
            out.append({
                "title": entry.get("title", ""),
                "summary": summary,
                "link": entry.get("link", ""),
                "source": feed.feed.get("title", feed_url),
                "published": entry.get("published", ""),
                "type": atype,
            })
    except Exception as e:
        print(f"  [{atype} feed error] {feed_url}: {e}")
    return out


def scrape_rss_feeds() -> list[dict]:
    """Pull latest news from the configured RSS feeds (user topic or AI default)."""
    articles = []
    for feed_url in get_news_feeds():
        articles += _articles_from_feed(feed_url, 5, "rss", summary_len=500)
    return articles


def scrape_research_feeds() -> list[dict]:
    """Pull latest research papers + AI lab blog posts.
    Tagged with type='research' for the RESEARCH badge in UI."""
    if not get_setting("use_research_feeds", True):
        return []

    def _add_authors(entry, summary):
        # arXiv entries have author info worth surfacing
        authors = ""
        if hasattr(entry, "authors"):
            authors = ", ".join(a.get("name", "") for a in entry.authors[:3])
        elif hasattr(entry, "author"):
            authors = entry.author
        return f"By {authors}. {summary}" if authors else summary

    articles = []
    for feed_url in RESEARCH_FEEDS:
        articles += _articles_from_feed(feed_url, 4, "research",
                                        summary_len=600, enrich=_add_authors)
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

        # Respect the user's configured content_topic. get_search_queries()
        # returns topic-derived queries (or the AI defaults when no topic is
        # set), matching every other discovery source.
        queries = get_search_queries()[:3] or [
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
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            )

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


def scrape_gmail_newsletters(gmail_service=None, session_file=None) -> list[dict]:
    """
    Extract AI news from Gmail newsletters.
    Requires Gmail API credentials (setup via gmail_setup.py).
    Falls back gracefully if not configured.

    session_file: optional Path to a specific Gmail token; defaults to the
    active Gmail session resolved from sessions.json.
    """
    articles = []
    creds_file = Path(session_file) if session_file else _get_active_gmail_session_file()

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
            pub_dt = pub_dt.astimezone().replace(tzinfo=None)
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

    # Deduplicate by URL when present, else by the FULL normalized title.
    # Keying on just the first 50 chars of the title collapsed distinct stories
    # that happen to share a long common prefix (templated / "Weekly roundup:"
    # titles), silently dropping the second — sometimes the fresher or
    # higher-quality copy. Using the URL (exact same link = same article) with a
    # normalized full-title fallback keeps genuinely different stories. (audit-8 3.D)
    seen_keys = set()
    unique = []
    for article in fresh:
        url = (article.get("link") or "").strip().lower()
        title_norm = " ".join((article.get("title") or "").split()).lower()
        key = url or title_norm
        if not key:
            unique.append(article)  # no URL and no title — don't collapse these together
            continue
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(article)

    # Sort by recency. `published` is stored in incompatible formats across
    # sources (RFC-822 from RSS, ISO-8601 from DDG/web/Gmail), so a plain
    # string sort is meaningless. Parse to a real datetime — reusing the same
    # parser _is_fresh relies on — and fall back to datetime.min when unparseable.
    from dateutil.parser import parse as _dp

    def _pub_key(a):
        try:
            dt = _dp(a.get("published", ""), fuzzy=True)
            return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return datetime.min

    unique.sort(key=_pub_key, reverse=True)

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
