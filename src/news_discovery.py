"""
News Discovery Agent — runs at 3 PM daily.
Scrapes news for your configured topic and stores them as swipeable discovery cards in SQLite.
User swipes right (like) or left (dislike) to train the preference algorithm.

Scoring uses:
  - TF-IDF weighted keywords (rare terms carry more signal)
  - Exponential time decay on swipe history (recent swipes matter more)
  - Audience insights from LinkedIn connections (sqrt-weighted categories)
  - Gmail newsletter topic signals (soft boost)
  - Profile.md interest checkboxes (hard block for [ ] unchecked topics)
  - Source quality auto-tracking (downweight sources you reject)
  - Topic clustering (one article per story, not three)
  - Epsilon-greedy exploration (15% random cards to prevent filter bubbles)
"""
import json
import math
import random
import re
import asyncio
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from config import BASE_DIR, EPSILON, PROFILE_FILE

import database as db


# ─── HTML & Text Cleanup ─────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    if "<" in text and ">" in text:
        try:
            soup = BeautifulSoup(text, "html.parser")
            text = soup.get_text(separator=" ")
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace + decode common entities
    text = re.sub(r"&\w+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ─── Stop Words ──────────────────────────────────────────────────────────────

STOP_WORDS = {
    # Articles, pronouns, basic verbs
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "and", "or", "but", "with", "from",
    "by", "as", "that", "this", "it", "its", "has", "have", "had", "will",
    "can", "could", "would", "should", "must", "may", "might", "shall",
    "do", "does", "did", "done", "doing",
    "not", "their", "they", "them", "than", "more", "also", "about", "into",
    "what", "how", "new", "says", "said", "after", "over", "just", "most",
    "now", "even", "some", "any", "all", "no", "yes", "only", "very",
    # Question words
    "who", "where", "when", "why", "which", "whose", "whom",
    # Conjunctions / prepositions
    "if", "then", "else", "so", "because", "while", "until", "before",
    "between", "through", "during", "above", "below", "out", "off", "down",
    "under", "again", "further", "such", "both", "each", "few", "other",
    # Months & days
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
    "nov", "dec", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "today",
    "tomorrow", "yesterday", "week", "month", "year", "years", "day", "days",
    # Common verbs people don't care about as keywords
    "comes", "come", "coming", "goes", "going", "gets", "got", "getting",
    "made", "make", "making", "take", "taking", "took", "give", "gave",
    "giving", "told", "tell", "telling", "see", "saw", "seen", "seeing",
    "know", "known", "want", "wanted", "need", "needed", "use", "used",
    "using", "say", "sayings", "puts", "put", "let", "lets", "letting",
    "look", "looking", "back", "way", "ways", "thing", "things", "stuff",
    "lot", "much", "many", "every",
    # HTML/RSS noise tokens that slip through
    "href", "https", "http", "com", "www", "html", "css", "rss", "xml",
    "feed", "amp", "nbsp", "src", "img", "alt", "div", "span", "para",
    "rel", "noopener", "noreferrer", "title", "link", "url",
    # Common LinkedIn/Twitter noise
    "post", "posts", "share", "shared", "follow", "following", "like",
    "liked", "subscribe", "comment", "comments", "view", "views",
    # Filler / hedging
    "really", "actually", "basically", "literally", "definitely", "probably",
    "maybe", "perhaps", "tough", "welcome", "full", "height", "width",
    "click", "read", "find", "found", "across", "around", "almost",
    # Single-letter or very short noise
    "don", "doesn", "won", "wouldn", "isn", "wasn", "aren", "weren", "haven",
    "hasn", "hadn", "ain", "let", "yeah", "well", "okay",
    # Pronouns missed
    "you", "your", "yours", "yourself", "i", "me", "my", "mine", "myself",
    "we", "us", "our", "ours", "ourselves", "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
}

# Common place names that shouldn't dominate keywords
PLACE_NAMES = {
    "san", "jose", "york", "francisco", "london", "paris", "tokyo", "berlin",
    "boston", "chicago", "seattle", "austin", "denver", "miami", "vegas",
    "angeles", "diego", "valley", "silicon", "bay", "europe", "asia", "africa",
    "america", "uk", "usa", "canada", "india", "china", "japan", "germany",
    "france", "spain", "italy", "russia", "brazil", "mexico", "egypt",
}
STOP_WORDS |= PLACE_NAMES


# ─── Profile Interests Parser ─────────────────────────────────────────────────

_PROFILE_CACHE = {"interests": None, "blacklist": None, "mtime": 0}

def _parse_profile_interests() -> tuple[list[str], list[str]]:
    """Read profile.md and extract:
      - active interests (lines starting with `- [x]`)
      - blacklist topics (lines starting with `- [ ]`)
    Returns (interests, blacklist) as keyword lists.
    Caches result; invalidates when profile.md changes."""
    if not PROFILE_FILE.exists():
        return [], []

    mtime = PROFILE_FILE.stat().st_mtime
    if (_PROFILE_CACHE["mtime"] == mtime
            and _PROFILE_CACHE["interests"] is not None):
        return _PROFILE_CACHE["interests"], _PROFILE_CACHE["blacklist"]

    text = PROFILE_FILE.read_text(encoding="utf-8")
    interests = []
    blacklist = []

    # Look for "## My Interests" section specifically
    in_interests = False
    for line in text.split("\n"):
        if line.strip().startswith("## My Interests"):
            in_interests = True
            continue
        if in_interests and line.startswith("## "):
            break
        if not in_interests:
            continue

        # Parse "- [x] topic name (extra info)"
        m = re.match(r"\s*-\s*\[([xX ])\]\s*(.+)", line)
        if not m:
            continue
        checked, raw_topic = m.groups()
        # Strip parenthetical asides and split into keywords
        topic = re.sub(r"\([^)]*\)", "", raw_topic).strip()
        # Extract meaningful words (drop short / stop words)
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", topic)
                 if w.lower() not in STOP_WORDS]
        target = interests if checked.lower() == "x" else blacklist
        target.extend(words)

    _PROFILE_CACHE["interests"] = interests
    _PROFILE_CACHE["blacklist"] = blacklist
    _PROFILE_CACHE["mtime"] = mtime
    return interests, blacklist


# ─── Keyword Extraction ──────────────────────────────────────────────────────

def extract_keywords(text: str, top_n: int = 5, source_title: str = "") -> list[str]:
    """Extract noun-like terms from text for preference tracking.
    Strips HTML, filters stop words, prefers proper nouns from title."""
    # Strip HTML first — was the main source of href/https/com noise
    text = strip_html(text)

    # Tokenize — letters only, length 3+
    words = re.findall(r"[A-Za-z]{3,}", text)
    if not words:
        return []

    # Frequency count, skipping stop words
    freq = {}
    for w in words:
        wl = w.lower()
        if wl in STOP_WORDS:
            continue
        # Skip all-uppercase short acronyms only if 2 chars (already filtered) ok
        freq[wl] = freq.get(wl, 0) + 1

    # Boost title-cased / capitalized words (likely proper nouns / topics)
    if source_title:
        title_words = re.findall(r"[A-Z][a-z]{2,}", source_title)
        for tw in title_words:
            wl = tw.lower()
            if wl in freq:
                freq[wl] += 2  # Title boost

    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [w for w, _ in ranked[:top_n]]


# ─── Discovery Pipeline ──────────────────────────────────────────────────────

def _get_known_urls() -> set:
    """Return the set of URLs we've already seen as discovery cards.
    Used to skip re-surfacing articles the user has already swiped on."""
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT url FROM discovery_cards WHERE url IS NOT NULL AND url != ''").fetchall()
        return {r["url"].strip() for r in rows if r["url"]}
    finally:
        conn.close()


async def discover_news() -> list[dict]:
    """Scrape news and store as discovery cards in SQLite.
    Applies TF-IDF scoring, time decay, profile blacklist, source quality,
    topic clustering, and epsilon-greedy exploration."""
    from news_scraper import gather_all_news

    print("[Discovery] Scraping news for your feed...")
    articles = await gather_all_news()

    if not articles:
        print("[Discovery] No articles found.")
        return []

    # Update TF-IDF corpus with new articles
    db.update_corpus(articles)

    # Pre-fetch all decayed weights (single pass, not per-card)
    kw_weights = db.get_all_keyword_weights()
    src_weights = db.get_all_source_weights()
    interests, blacklist = _parse_profile_interests()

    cards = []
    # Include hour+minute so multiple discovery runs in a day produce
    # unique card IDs (otherwise re-runs just return already-swiped cards)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M")

    # Topic clustering: dedupe near-duplicate articles before scoring
    articles = _cluster_articles(articles)

    # Also dedupe against articles whose URL we've ALREADY seen in the DB
    # (regardless of card_id), so the user doesn't keep seeing the same story
    seen_urls = set(_get_known_urls())

    candidate_articles = []
    for article in articles:
        url = article.get("link", "").strip()
        if url and url in seen_urls:
            continue
        candidate_articles.append(article)

    for i, article in enumerate(candidate_articles[:25]):  # Process top 25 candidates
        card_id = f"disc_{run_stamp}_{i:03d}"

        # Skip if card already exists
        if db.card_exists(card_id):
            existing = db.get_card(card_id)
            if existing:
                cards.append(existing)
            continue

        title = article.get("title", "")
        # Strip HTML from summary BEFORE storing
        summary = strip_html(article.get("summary", ""))[:400]
        keywords = extract_keywords(f"{title} {summary}", top_n=5, source_title=title)
        source = article.get("source", "Unknown")
        article_type = article.get("type", "rss")

        # Calculate score
        score = _calculate_score(
            keywords, source, article_type,
            kw_weights, src_weights, interests, blacklist
        )

        card = {
            "id": card_id,
            "title": title,
            "summary": summary[:300],
            "source": source,
            "url": article.get("link", ""),
            "type": article_type,
            "keywords": keywords,
            "preference_score": round(score, 2),
            "is_exploration": 0,
            "discovered_at": datetime.now().isoformat(),
            "status": "pending",
        }

        db.insert_card(card)
        cards.append(card)

    if not cards:
        print("[Discovery] No new cards to process.")
        return []

    # Cap to 15 final cards, apply epsilon-greedy exploration
    cards = sorted(cards, key=lambda c: c.get("preference_score", 0), reverse=True)[:15]
    cards = _apply_epsilon_greedy(cards)

    print(f"[Discovery] Prepared {len(cards)} discovery cards.")
    return cards


def _cluster_articles(articles: list[dict]) -> list[dict]:
    """Group near-duplicate articles by title similarity, keep one per cluster.
    Uses simple Jaccard similarity on title bigrams. Threshold: 0.5 overlap."""
    def title_tokens(t: str) -> set:
        words = re.findall(r"[A-Za-z]{3,}", strip_html(t).lower())
        words = [w for w in words if w not in STOP_WORDS]
        return set(words)

    clusters: list[list[dict]] = []
    for art in articles:
        tokens = title_tokens(art.get("title", ""))
        if not tokens:
            clusters.append([art])
            continue
        placed = False
        for cluster in clusters:
            ref_tokens = title_tokens(cluster[0].get("title", ""))
            if not ref_tokens:
                continue
            jaccard = len(tokens & ref_tokens) / max(len(tokens | ref_tokens), 1)
            if jaccard >= 0.5:
                cluster.append(art)
                placed = True
                break
        if not placed:
            clusters.append([art])

    # Pick the article from each cluster with the longest summary (most info)
    result = []
    for cluster in clusters:
        best = max(cluster, key=lambda a: len(a.get("summary", "")))
        result.append(best)
    return result


def _calculate_score(keywords: list, source: str, article_type: str,
                     kw_weights: dict, src_weights: dict,
                     interests: list, blacklist: list) -> float:
    """Score an article using TF-IDF weighted, time-decayed preferences
    plus audience insights, Gmail signals, and profile interest enforcement."""
    score = 0.0
    kw_set = {kw.lower() for kw in keywords}

    # ─── Profile interests boost / blacklist ─────────────────────────────
    interest_hits = sum(1 for w in interests if w in kw_set)
    blacklist_hits = sum(1 for w in blacklist if w in kw_set)

    if interest_hits > 0:
        score += interest_hits * 3.0  # Strong boost for declared interests

    if blacklist_hits > 0:
        # Soft block: heavy penalty but not zero (exploration may surface)
        score -= blacklist_hits * 10.0

    # ─── TF-IDF weighted keyword preferences (time-decayed) ──────────────
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in kw_weights:
            score += kw_weights[kw_lower]

    # ─── Source preferences (time-decayed, 2x weight, source quality) ────
    src_lower = source.lower()
    if src_lower in src_weights:
        score += src_weights[src_lower]
    # Multiply by source quality (0.0-1.0) — auto-prune low-quality sources
    quality = db.get_source_quality(source)
    if quality < 0.3:
        score -= 1.5  # Source has been mostly rejected

    # ─── Research papers get a small bonus (signal of depth) ─────────────
    if article_type in ("research", "arxiv"):
        score += 1.5

    # ─── Audience insights — proportional sqrt-weighted boost ────────────
    insights_file = BASE_DIR / "audience_insights.json"
    if insights_file.exists():
        try:
            with open(insights_file, "r", encoding="utf-8") as f:
                insights = json.load(f)

            audience_kws = {t["word"].lower() for t in insights.get("top_titles", [])[:20]}
            for kw in keywords:
                if kw.lower() in audience_kws:
                    score += 0.5

            breakdown = insights.get("breakdown", {})
            for cat, info in breakdown.items():
                if cat == "other":
                    continue
                pct = info.get("percentage", 0)
                cat_weight = max(0.2, (pct ** 0.5) * 0.1)
                for headline in info.get("sample_headlines", []):
                    headline_words = set(re.findall(r"\w+", strip_html(headline).lower()))
                    for kw in keywords:
                        if kw.lower() in headline_words:
                            score += cat_weight
        except Exception:
            pass

    # ─── Gmail newsletter topic signals (soft 0.3x boost) ────────────────
    gmail_prefs = _extract_gmail_preferences()
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in gmail_prefs:
            score += gmail_prefs[kw_lower] * 0.3

    return score


def _apply_epsilon_greedy(cards: list[dict]) -> list[dict]:
    """Epsilon-greedy: top (1-epsilon) by score + epsilon random exploration.
    Exploration cards are flagged so the user knows they're outside their bubble."""
    if len(cards) <= 3:
        return cards

    n_explore = max(1, int(len(cards) * EPSILON))
    n_exploit = len(cards) - n_explore

    scored = sorted(cards, key=lambda c: c.get("preference_score", 0), reverse=True)
    exploit_cards = scored[:n_exploit]
    remaining = scored[n_exploit:]

    if remaining:
        explore_cards = random.sample(remaining, min(n_explore, len(remaining)))
        conn = db.get_conn()
        try:
            for card in explore_cards:
                card["is_exploration"] = 1
                conn.execute(
                    "UPDATE discovery_cards SET is_exploration = 1 WHERE id = ?",
                    (card["id"],)
                )
            conn.commit()
        finally:
            conn.close()
    else:
        explore_cards = []

    # Interleave: put exploration cards at random positions within exploit list
    result = list(exploit_cards)
    for ec in explore_cards:
        pos = random.randint(1, max(1, len(result)))
        result.insert(pos, ec)

    return result


def _extract_gmail_preferences() -> dict:
    """Extract topic keywords from Gmail newsletter subjects for soft preference signals."""
    cache_file = BASE_DIR / "gmail_keyword_cache.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cached_at = datetime.fromisoformat(cache.get("cached_at", "2000-01-01"))
            if (datetime.now() - cached_at).total_seconds() < 21600:
                return cache.get("keywords", {})
        except Exception:
            pass

    keywords = {}
    gmail_data_file = BASE_DIR / "gmail_articles.json"
    if gmail_data_file.exists():
        try:
            with open(gmail_data_file, "r", encoding="utf-8") as f:
                articles = json.load(f)
            for art in articles:
                title = art.get("title", "") or art.get("subject", "")
                summary = art.get("summary", "") or art.get("snippet", "")
                extracted = extract_keywords(f"{title} {summary}", top_n=5)
                for kw in extracted:
                    keywords[kw] = keywords.get(kw, 0) + 0.5
        except Exception:
            pass

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "keywords": keywords}, f)
    except Exception:
        pass

    return keywords


# ─── Public API (used by approval_server.py) ─────────────────────────────────

def record_swipe(card_id: str, action: str) -> bool:
    """Record a like or dislike — delegates to database module."""
    return db.record_swipe_db(card_id, action)


def get_pending_discovery() -> list[dict]:
    """Get all unswiped discovery cards."""
    return db.get_pending_cards()


def get_discovery_stats() -> dict:
    """Get stats about discovery activity."""
    return db.get_stats()


# ─── Legacy compatibility ────────────────────────────────────────────────────

def load_preferences() -> dict:
    """Load preferences — now backed by SQLite time-decayed weights."""
    kw_weights = db.get_all_keyword_weights()
    src_weights = db.get_all_source_weights()
    stats = db.get_stats()

    return {
        "liked_keywords": {k: v for k, v in kw_weights.items() if v > 0},
        "disliked_keywords": {k: abs(v) for k, v in kw_weights.items() if v < 0},
        "liked_sources": {k: v for k, v in src_weights.items() if v > 0},
        "disliked_sources": {k: abs(v) for k, v in src_weights.items() if v < 0},
        "history": db.get_swipe_history(),
        "total_liked": stats["total_liked"],
        "total_disliked": stats["total_disliked"],
    }


def save_preferences(prefs: dict):
    """No-op — preferences are now stored in SQLite via swipe events."""
    pass


if __name__ == "__main__":
    cards = asyncio.run(discover_news())
    print(f"\nDiscovered {len(cards)} cards.")
    for c in cards[:5]:
        tag = " [EXPLORE]" if c.get("is_exploration") else ""
        print(f"  [{c['source']}] {c['title'][:50]}... (score: {c['preference_score']}){tag}")
