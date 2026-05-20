"""Central configuration for LinkedIn Auto-Poster."""
import os
import json
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
AUTH_DIR = BASE_DIR / "auth_state"
POSTS_DIR = BASE_DIR / "posts"
IMAGES_DIR = BASE_DIR / "images"
LOGS_DIR = BASE_DIR / "logs"
PROFILE_FILE = BASE_DIR / "profile.md"
SETTINGS_FILE = BASE_DIR / "settings.json"

# Ensure directories exist
for d in (POSTS_DIR, IMAGES_DIR, LOGS_DIR, AUTH_DIR):
    d.mkdir(exist_ok=True)

# ─── Default settings (overridable via settings.json) ────────────────────────

DEFAULT_SETTINGS = {
    "discovery_time": "15:00",       # 3 PM — scrape news
    "generation_time": "17:00",      # 5 PM — Claude generates post
    "reminder_interval_minutes": 30,
    "max_cards_per_day": 15,
    "default_post_type": "news",     # news | builder | hot_take | research | audience_hook
    "allow_robotics": True,          # Allow occasional robotics in exploration
    "use_research_feeds": True,
    "auto_open_dashboard": True,
    "github_username": "",           # For builder posts — pulls recent commits
    "gmail_method": "none",          # "claude_mcp" | "credentials" | "none"
    "gen_engine": "claude_code",     # "claude_code" | "anthropic_api"
    "anthropic_api_key": "",         # Only needed when gen_engine = anthropic_api
    "anthropic_model": "claude-sonnet-4-20250514",

    # ─── Content niche (post about ANYTHING, not just AI/tech) ───────────────
    "content_topic": "AI and technology",  # free text — your niche / what you post about
    "custom_feeds": [],              # list of RSS URLs; empty = built-in defaults below
    "custom_search_queries": [],     # list of web-search queries; empty = derived from topic
    "setup_completed": False,        # first-run wizard flag
}


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                user_settings = json.load(f)
            merged = {**DEFAULT_SETTINGS, **user_settings}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


_SETTINGS = _load_settings()


def get_setting(key: str, default=None):
    """Get a setting value (re-reads file each time so UI changes propagate)."""
    settings = _load_settings()
    return settings.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))


def save_settings(settings: dict) -> None:
    """Persist settings to settings.json (merges with defaults)."""
    merged = {**DEFAULT_SETTINGS, **settings}
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)


# ─── Scheduling (kept for backward compat — prefer get_setting()) ────────────

POST_TIME = _SETTINGS["generation_time"]
DISCOVERY_TIME = _SETTINGS["discovery_time"]
REMINDER_INTERVAL_MINUTES = _SETTINGS["reminder_interval_minutes"]

# Discovery & Preferences (legacy — kept for migration)
DISCOVERY_DIR = POSTS_DIR / "discovery"
PREFERENCES_FILE = BASE_DIR / "preferences.json"

# SQLite database
DB_FILE = BASE_DIR / "discovery.db"

# TF-IDF & Time Decay
DECAY_LAMBDA = 0.02
EPSILON = 0.15

# Claude API key — kept for legacy / optional fallback (we use Claude Code CLI now)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Multi-session
SESSIONS_FILE = AUTH_DIR / "sessions.json"
MAX_LINKEDIN_SESSIONS = 3
MAX_GMAIL_SESSIONS = 3

# Approval server
APPROVAL_HOST = "127.0.0.1"
APPROVAL_PORT = 5555
APPROVAL_URL = f"http://{APPROVAL_HOST}:{APPROVAL_PORT}"

# ─── News sources ────────────────────────────────────────────────────────────

# Primary AI/dev news feeds
# (LLMs, AI coding tools, AI agents, dev tools, open source AI, startups)
AI_NEWS_FEEDS = [
    # Vendor blogs (official source signals)
    "https://www.anthropic.com/news/rss.xml",
    "https://openai.com/blog/rss.xml",
    "https://blog.google/technology/ai/rss/",
    "https://vercel.com/blog/feed.xml",

    # Dev / LLM community
    "https://simonwillison.net/atom/everything/",
    "https://www.latent.space/feed",
    "https://news.ycombinator.com/rss",
    "https://github.blog/feed/",

    # AI/tech news (curated, not generic)
    "https://venturebeat.com/category/ai/feed/",
    "https://the-decoder.com/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://feeds.feedburner.com/TheHackersNews",
]

# Research paper feeds (separate type — gets RESEARCH badge)
RESEARCH_FEEDS = [
    # arXiv categories
    "http://export.arxiv.org/rss/cs.AI",       # Artificial Intelligence
    "http://export.arxiv.org/rss/cs.CL",       # Computation & Language (NLP/LLMs)
    "http://export.arxiv.org/rss/cs.LG",       # Machine Learning

    # Research labs
    "https://research.google/blog/rss/",
    "https://news.mit.edu/topic/mitcsail-rss.xml",
    "https://hai.stanford.edu/news/rss.xml",

    # Curated papers
    "https://huggingface.co/papers/rss",
]

# Web search keywords for scraping
AI_SEARCH_QUERIES = [
    "AI news today",
    "artificial intelligence breakthrough 2026",
    "large language model news",
    "AI agent framework news",
    "OpenAI Anthropic Google AI update",
]

# Image sources (free, no-login-required)
IMAGE_SOURCES = {
    "unsplash": "https://unsplash.com/s/photos/{query}",
    "pexels": "https://www.pexels.com/search/{query}/",
    "pixabay": "https://pixabay.com/images/search/{query}/",
}

# Gmail label to watch for newsletters
GMAIL_AI_LABELS = ["AI News", "Newsletters", "INBOX"]
GMAIL_SEARCH_QUERY = "subject:(AI OR artificial intelligence OR LLM OR GPT OR Claude) newer_than:1d"


# ─── Topic resolution (lets the user post about ANY niche) ───────────────────
# These read the user's settings and fall back to the AI/tech defaults above,
# so an unconfigured install behaves exactly as before.

def get_content_topic() -> str:
    return (get_setting("content_topic", "AI and technology") or "AI and technology").strip()


def get_news_feeds() -> list:
    """User's custom RSS feeds if set, else the built-in AI/tech feeds."""
    custom = get_setting("custom_feeds", []) or []
    custom = [str(u).strip() for u in custom if str(u).strip()]
    return custom if custom else AI_NEWS_FEEDS


def get_search_queries() -> list:
    """User's custom web-search queries if set, else queries derived from the topic."""
    custom = get_setting("custom_search_queries", []) or []
    custom = [str(q).strip() for q in custom if str(q).strip()]
    if custom:
        return custom
    topic = get_content_topic()
    if topic.lower() in ("ai and technology", "ai", "tech", "technology"):
        return AI_SEARCH_QUERIES
    return [
        f"{topic} news today",
        f"{topic} latest news",
        f"{topic} trends 2026",
        f"{topic} update",
    ]


def get_gmail_search_query() -> str:
    """Gmail search scoped to the user's topic (falls back to the AI query)."""
    topic = get_content_topic()
    if topic.lower() in ("ai and technology", "ai", "tech", "technology"):
        return GMAIL_SEARCH_QUERY
    # Build a loose subject filter from the topic words
    words = [w for w in topic.replace("/", " ").replace(",", " ").split() if len(w) > 2]
    if not words:
        return GMAIL_SEARCH_QUERY
    subject = " OR ".join(words[:6])
    return f"subject:({subject}) newer_than:3d"
