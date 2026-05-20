"""
SQLite database for discovery cards, swipe events, and TF-IDF corpus stats.
Replaces per-card JSON files with a single fast queryable database.
"""
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from config import BASE_DIR

DB_PATH = BASE_DIR / "discovery.db"

# Decay rate: 0.02 => half-life ~35 days (ln(2)/0.02 = 34.7)
DECAY_LAMBDA = 0.02
# Epsilon for exploration: 15% random cards
EPSILON = 0.15
# Dislike dampener — left-swipe often means "meh" not "hate it"
# This stops the weights from going net-negative when user is mildly choosy
DISLIKE_DAMPER = 0.6
# Auto source-quality threshold — N consecutive rejects = 50% downweight next round
SOURCE_REJECT_THRESHOLD = 5


def get_conn() -> sqlite3.Connection:
    """Get a connection with row_factory for dict-like access."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_cards (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT,
            source TEXT,
            url TEXT,
            type TEXT DEFAULT 'rss',
            keywords TEXT,          -- JSON array
            preference_score REAL DEFAULT 0.0,
            is_exploration INTEGER DEFAULT 0,
            discovered_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            swiped_at TEXT
        );

        CREATE TABLE IF NOT EXISTS swipe_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            action TEXT NOT NULL,
            swiped_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            swiped_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS corpus_stats (
            keyword TEXT PRIMARY KEY,
            doc_count INTEGER DEFAULT 1,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS rejection_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_cards_status ON discovery_cards(status);
        CREATE INDEX IF NOT EXISTS idx_cards_discovered ON discovery_cards(discovered_at);
        CREATE INDEX IF NOT EXISTS idx_swipe_keyword ON swipe_events(keyword);
        CREATE INDEX IF NOT EXISTS idx_swipe_time ON swipe_events(swiped_at);
        CREATE INDEX IF NOT EXISTS idx_source_events ON source_events(source);
        CREATE INDEX IF NOT EXISTS idx_rejection_draft ON rejection_reasons(draft_id);
    """)
    conn.commit()
    conn.close()


# ─── TF-IDF Corpus ────────────────────────────────────────────────────────────

def update_corpus(articles: list[dict]):
    """Update IDF corpus stats from a batch of scraped articles.
    Called every time we scrape new articles."""
    conn = get_conn()

    # Get current total docs count
    row = conn.execute("SELECT value FROM meta WHERE key='total_docs'").fetchone()
    total_docs = int(row["value"]) if row else 0

    now = datetime.now().isoformat()
    new_docs = 0

    for article in articles:
        title = article.get("title", "")
        summary = article.get("summary", "")
        text = f"{title} {summary}".lower()
        # Extract unique keywords from this doc
        from news_discovery import extract_keywords
        keywords = set(kw.lower() for kw in extract_keywords(text, top_n=15))

        if keywords:
            new_docs += 1
            for kw in keywords:
                conn.execute("""
                    INSERT INTO corpus_stats (keyword, doc_count, updated_at)
                    VALUES (?, 1, ?)
                    ON CONFLICT(keyword) DO UPDATE SET
                        doc_count = doc_count + 1,
                        updated_at = ?
                """, (kw, now, now))

    total_docs += new_docs
    conn.execute("""
        INSERT INTO meta (key, value) VALUES ('total_docs', ?)
        ON CONFLICT(key) DO UPDATE SET value = ?
    """, (str(total_docs), str(total_docs)))

    conn.commit()
    conn.close()


def get_idf(keyword: str) -> float:
    """Get IDF score for a keyword. Higher = rarer = more valuable."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM meta WHERE key='total_docs'").fetchone()
    total_docs = int(row["value"]) if row else 1

    row = conn.execute(
        "SELECT doc_count FROM corpus_stats WHERE keyword = ?", (keyword.lower(),)
    ).fetchone()
    doc_count = row["doc_count"] if row else 0
    conn.close()

    # IDF = log(N / (1 + df)) — smoothed to avoid division by zero
    # Rare keyword (df=1, N=500): IDF = log(500/2) = 5.5
    # Common keyword (df=200, N=500): IDF = log(500/201) = 0.91
    return math.log(max(total_docs, 1) / (1 + doc_count))


def get_idf_batch(keywords: list[str]) -> dict[str, float]:
    """Get IDF scores for multiple keywords at once. Much faster than individual calls."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM meta WHERE key='total_docs'").fetchone()
    total_docs = max(int(row["value"]) if row else 1, 1)

    result = {}
    placeholders = ",".join("?" for _ in keywords)
    if placeholders:
        rows = conn.execute(
            f"SELECT keyword, doc_count FROM corpus_stats WHERE keyword IN ({placeholders})",
            [kw.lower() for kw in keywords]
        ).fetchall()
        doc_counts = {r["keyword"]: r["doc_count"] for r in rows}
    else:
        doc_counts = {}
    conn.close()

    for kw in keywords:
        dc = doc_counts.get(kw.lower(), 0)
        result[kw.lower()] = math.log(total_docs / (1 + dc))

    return result


# ─── Time-Decayed Keyword Weights ─────────────────────────────────────────────

def get_keyword_weight(keyword: str) -> float:
    """Calculate time-decayed weight for a keyword across all swipes.
    W = Σ (sign * IDF * e^(-λ * Δt))
    where sign = +1 for liked, -1 for disliked
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT action, swiped_at FROM swipe_events WHERE keyword = ?",
        (keyword.lower(),)
    ).fetchall()
    conn.close()

    if not rows:
        return 0.0

    idf = get_idf(keyword)
    now = datetime.now()
    weight = 0.0

    for row in rows:
        sign = 1.0 if row["action"] == "liked" else -1.0
        try:
            swiped_at = datetime.fromisoformat(row["swiped_at"])
            delta_days = (now - swiped_at).total_seconds() / 86400
        except (ValueError, TypeError):
            delta_days = 0

        # W_adjusted = sign * IDF * e^(-λ * Δt)
        weight += sign * idf * math.exp(-DECAY_LAMBDA * delta_days)

    return weight


def get_all_keyword_weights() -> dict[str, float]:
    """Get time-decayed weights for ALL keywords with swipe history.
    Returns {keyword: decayed_weight} dict."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT keyword, action, swiped_at FROM swipe_events ORDER BY keyword"
    ).fetchall()
    conn.close()

    if not rows:
        return {}

    # Group by keyword
    keyword_events: dict[str, list] = {}
    for row in rows:
        kw = row["keyword"]
        keyword_events.setdefault(kw, []).append(row)

    # Batch fetch IDF scores
    idf_scores = get_idf_batch(list(keyword_events.keys()))

    now = datetime.now()
    weights = {}

    for kw, events in keyword_events.items():
        idf = idf_scores.get(kw, 1.0)
        w = 0.0
        for ev in events:
            # Damped dislike — left-swipe = "not now" not "never again"
            sign = 1.0 if ev["action"] == "liked" else -DISLIKE_DAMPER
            try:
                swiped_at = datetime.fromisoformat(ev["swiped_at"])
                delta_days = (now - swiped_at).total_seconds() / 86400
            except (ValueError, TypeError):
                delta_days = 0
            w += sign * idf * math.exp(-DECAY_LAMBDA * delta_days)
        weights[kw] = w

    return weights


def get_source_weight(source: str) -> float:
    """Calculate time-decayed weight for a source."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT action, swiped_at FROM source_events WHERE source = ?",
        (source.lower(),)
    ).fetchall()
    conn.close()

    if not rows:
        return 0.0

    now = datetime.now()
    weight = 0.0
    for row in rows:
        sign = 1.0 if row["action"] == "liked" else -1.0
        try:
            swiped_at = datetime.fromisoformat(row["swiped_at"])
            delta_days = (now - swiped_at).total_seconds() / 86400
        except (ValueError, TypeError):
            delta_days = 0
        weight += sign * math.exp(-DECAY_LAMBDA * delta_days)

    return weight * 2  # Sources weighted 2x


def get_all_source_weights() -> dict[str, float]:
    """Get time-decayed weights for ALL sources."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT source, action, swiped_at FROM source_events"
    ).fetchall()
    conn.close()

    if not rows:
        return {}

    source_events: dict[str, list] = {}
    for row in rows:
        src = row["source"]
        source_events.setdefault(src, []).append(row)

    now = datetime.now()
    weights = {}
    for src, events in source_events.items():
        w = 0.0
        # Sort events by time so we can detect consecutive rejects
        events_sorted = sorted(events, key=lambda e: e["swiped_at"])
        consecutive_rejects = 0
        for ev in events_sorted:
            if ev["action"] == "liked":
                consecutive_rejects = 0
                sign = 1.0
            else:
                consecutive_rejects += 1
                sign = -DISLIKE_DAMPER
            try:
                swiped_at = datetime.fromisoformat(ev["swiped_at"])
                delta_days = (now - swiped_at).total_seconds() / 86400
            except (ValueError, TypeError):
                delta_days = 0
            w += sign * math.exp(-DECAY_LAMBDA * delta_days)

        # Hard penalty for sources with N consecutive recent rejects
        if consecutive_rejects >= SOURCE_REJECT_THRESHOLD:
            w -= 2.0

        weights[src] = w * 2  # Sources weighted 2x

    return weights


def get_source_quality(source: str) -> float:
    """Get a 0.0-1.0 quality score for a source based on like/dislike ratio.
    Used to auto-prune low-quality sources from discovery."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT action FROM source_events WHERE source = ? ORDER BY swiped_at DESC LIMIT 20",
        (source.lower(),)
    ).fetchall()
    conn.close()

    if not rows:
        return 0.5  # Unknown source — neutral

    likes = sum(1 for r in rows if r["action"] == "liked")
    return likes / len(rows)


# ─── Discovery Card CRUD ─────────────────────────────────────────────────────

def insert_card(card: dict):
    """Insert a discovery card into the database."""
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO discovery_cards
            (id, title, summary, source, url, type, keywords, preference_score,
             is_exploration, discovered_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        card["id"], card["title"], card.get("summary", ""),
        card.get("source", "Unknown"), card.get("url", ""),
        card.get("type", "rss"), json.dumps(card.get("keywords", [])),
        card.get("preference_score", 0.0), card.get("is_exploration", 0),
        card["discovered_at"], card.get("status", "pending"),
    ))
    conn.commit()
    conn.close()


def get_card(card_id: str) -> dict | None:
    """Get a single card by ID."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM discovery_cards WHERE id = ?", (card_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_card(row)


def get_pending_cards() -> list[dict]:
    """Get all unswiped discovery cards, newest first."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM discovery_cards
        WHERE status = 'pending'
        ORDER BY preference_score DESC, discovered_at DESC
    """).fetchall()
    conn.close()
    return [_row_to_card(r) for r in rows]


def get_today_liked_cards() -> list[dict]:
    """Get cards liked today — for the 5 PM post selection."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM discovery_cards
        WHERE status = 'liked' AND swiped_at LIKE ?
        ORDER BY preference_score DESC
    """, (f"{today}%",)).fetchall()
    conn.close()
    return [_row_to_card(r) for r in rows]


def get_today_pending_cards() -> list[dict]:
    """Get un-swiped cards from today — fallback pool."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM discovery_cards
        WHERE status = 'pending' AND discovered_at LIKE ?
        ORDER BY preference_score DESC
    """, (f"{today}%",)).fetchall()
    conn.close()
    return [_row_to_card(r) for r in rows]


def record_swipe_db(card_id: str, action: str) -> bool:
    """Record a like or dislike. Inserts keyword+source events for decay tracking."""
    conn = get_conn()

    row = conn.execute(
        "SELECT * FROM discovery_cards WHERE id = ?", (card_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False

    now = datetime.now().isoformat()

    # Update card status
    conn.execute("""
        UPDATE discovery_cards SET status = ?, swiped_at = ? WHERE id = ?
    """, (action, now, card_id))

    # Insert per-keyword swipe events (for time-decayed weights)
    keywords = json.loads(row["keywords"]) if row["keywords"] else []
    for kw in keywords:
        conn.execute("""
            INSERT INTO swipe_events (card_id, keyword, action, swiped_at)
            VALUES (?, ?, ?, ?)
        """, (card_id, kw.lower(), action, now))

    # Insert source event
    source = (row["source"] or "").lower()
    if source:
        conn.execute("""
            INSERT INTO source_events (card_id, source, action, swiped_at)
            VALUES (?, ?, ?, ?)
        """, (card_id, source, action, now))

    conn.commit()
    conn.close()
    return True


def card_exists(card_id: str) -> bool:
    """Check if a card already exists (avoid duplicates on re-scrape)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM discovery_cards WHERE id = ?", (card_id,)
    ).fetchone()
    conn.close()
    return row is not None


def get_stats() -> dict:
    """Get discovery statistics."""
    conn = get_conn()

    pending = conn.execute(
        "SELECT COUNT(*) as c FROM discovery_cards WHERE status='pending'"
    ).fetchone()["c"]

    total_liked = conn.execute(
        "SELECT COUNT(*) as c FROM discovery_cards WHERE status='liked'"
    ).fetchone()["c"]

    total_disliked = conn.execute(
        "SELECT COUNT(*) as c FROM discovery_cards WHERE status='disliked'"
    ).fetchone()["c"]

    # Top liked keywords (by time-decayed weight)
    kw_weights = get_all_keyword_weights()
    top_liked = sorted(
        [(kw, w) for kw, w in kw_weights.items() if w > 0],
        key=lambda x: x[1], reverse=True
    )[:10]

    top_disliked = sorted(
        [(kw, abs(w)) for kw, w in kw_weights.items() if w < 0],
        key=lambda x: x[1], reverse=True
    )[:5]

    conn.close()

    return {
        "pending_cards": pending,
        "total_liked": total_liked,
        "total_disliked": total_disliked,
        "top_liked": [(kw, round(w, 2)) for kw, w in top_liked],
        "top_disliked": [(kw, round(w, 2)) for kw, w in top_disliked],
    }


def get_swipe_history(limit: int = 200) -> list[dict]:
    """Get recent swipe history for display."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, title, status as action, swiped_at as date
        FROM discovery_cards
        WHERE status IN ('liked', 'disliked')
        ORDER BY swiped_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Rejection Reasons ───────────────────────────────────────────────────────

def save_rejection_reason(draft_id: str, reason: str):
    """Save a rejection reason for feedback into next generation."""
    if not reason or not reason.strip():
        return
    conn = get_conn()
    conn.execute(
        "INSERT INTO rejection_reasons (draft_id, reason, created_at) VALUES (?, ?, ?)",
        (draft_id, reason.strip(), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_recent_rejection_reasons(limit: int = 5) -> list[str]:
    """Get the N most recent rejection reasons for prompt feedback loop."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT reason FROM rejection_reasons ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [r["reason"] for r in rows]


def _row_to_card(row: sqlite3.Row) -> dict:
    """Convert a DB row to a card dict matching the old JSON format."""
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row["summary"],
        "source": row["source"],
        "url": row["url"],
        "type": row["type"],
        "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
        "preference_score": row["preference_score"],
        "is_exploration": bool(row["is_exploration"]),
        "discovered_at": row["discovered_at"],
        "status": row["status"],
        "swiped_at": row["swiped_at"],
    }


# ─── Migration: JSON files -> SQLite ─────────────────────────────────────────

def migrate_from_json():
    """One-time migration: import existing JSON card files and preferences into SQLite."""
    from config import DISCOVERY_DIR, PREFERENCES_FILE

    init_db()
    conn = get_conn()
    migrated = 0

    # Migrate discovery card JSON files
    if DISCOVERY_DIR.exists():
        for f in DISCOVERY_DIR.glob("disc_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    card = json.load(fp)
                conn.execute("""
                    INSERT OR IGNORE INTO discovery_cards
                        (id, title, summary, source, url, type, keywords,
                         preference_score, discovered_at, status, swiped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    card["id"], card.get("title", ""), card.get("summary", ""),
                    card.get("source", ""), card.get("url", ""),
                    card.get("type", "rss"),
                    json.dumps(card.get("keywords", [])),
                    card.get("preference_score", 0),
                    card.get("discovered_at", datetime.now().isoformat()),
                    card.get("status", "pending"),
                    card.get("swiped_at"),
                ))
                migrated += 1
            except Exception as e:
                print(f"  [Migrate] Skipping {f.name}: {e}")

    # Migrate preferences.json -> swipe_events
    if PREFERENCES_FILE.exists():
        try:
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as fp:
                prefs = json.load(fp)

            # Import history as swipe events
            for entry in prefs.get("history", []):
                card_id = entry.get("card_id", "")
                action = entry.get("action", "")
                date = entry.get("date", datetime.now().isoformat())

                if not card_id or action not in ("liked", "disliked"):
                    continue

                # Try to get keywords from the card
                row = conn.execute(
                    "SELECT keywords, source FROM discovery_cards WHERE id = ?",
                    (card_id,)
                ).fetchone()

                if row:
                    keywords = json.loads(row["keywords"]) if row["keywords"] else []
                    source = row["source"] or ""
                else:
                    keywords = []
                    source = ""

                for kw in keywords:
                    conn.execute("""
                        INSERT INTO swipe_events (card_id, keyword, action, swiped_at)
                        VALUES (?, ?, ?, ?)
                    """, (card_id, kw.lower(), action, date))

                if source:
                    conn.execute("""
                        INSERT INTO source_events (card_id, source, action, swiped_at)
                        VALUES (?, ?, ?, ?)
                    """, (card_id, source.lower(), action, date))

        except Exception as e:
            print(f"  [Migrate] Preferences migration error: {e}")

    # Build initial corpus stats from all migrated cards
    rows = conn.execute("SELECT keywords FROM discovery_cards").fetchall()
    total_docs = len(rows)
    now = datetime.now().isoformat()

    for row in rows:
        keywords = json.loads(row["keywords"]) if row["keywords"] else []
        for kw in set(kw.lower() for kw in keywords):
            conn.execute("""
                INSERT INTO corpus_stats (keyword, doc_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    doc_count = doc_count + 1,
                    updated_at = ?
            """, (kw, now, now))

    conn.execute("""
        INSERT INTO meta (key, value) VALUES ('total_docs', ?)
        ON CONFLICT(key) DO UPDATE SET value = ?
    """, (str(total_docs), str(total_docs)))

    conn.commit()
    conn.close()
    print(f"  [Migrate] Imported {migrated} cards into SQLite.")


# Initialize on import
init_db()
