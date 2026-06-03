"""
Generates LinkedIn posts from scraped AI news using **Claude Code CLI**.
No Anthropic API key needed — uses your existing Claude Code subscription.

Pipeline:
  1. pick_best_topic() — picks article from today's liked cards (or fallbacks)
  2. _build_post_context() — assembles _post_context.md with article + profile +
     past posts + audience + rejection feedback + anti-AI rules
  3. _run_claude_code() — spawns `claude -p` subprocess to read context and write draft
  4. Returns the draft Claude wrote (parsed from posts/draft_<timestamp>.json)

Fallback: if Claude CLI fails (not installed, auth expired, timeout), falls
back to a smart template that's much better than the old "Worth watching..." stub.

Article selection priority:
  1. Today's liked cards (from discovery swipes)
  2. Tie-break by audience alignment
  3. Highest-scored pending cards
  4. Raw scraped articles (final fallback)
"""
import json
import os
import re
import math
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from config import PROFILE_FILE, POSTS_DIR, IMAGE_SOURCES, BASE_DIR

AUDIENCE_FILE = BASE_DIR / "audience_insights.json"
PAST_POSTS_FILE = BASE_DIR / "my_past_posts.md"
CONTEXT_TEMPLATE = BASE_DIR / "_post_context_template.md"
CONTEXT_FILE = BASE_DIR / "_post_context.md"

# ─── Subprocess timeout (in seconds) for Claude Code CLI calls ───────────────
CLAUDE_TIMEOUT_SECONDS = 240

# ─── Progress callback (set by approval_server at runtime) ────────────────────
_progress_callback = None

def _step(icon: str, text: str):
    """Report a generation step if a callback is registered."""
    if _progress_callback:
        try:
            _progress_callback(icon, text)
        except Exception:
            pass
    print(f"[Generator] {text}")


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities from text."""
    from bs4 import BeautifulSoup
    if not text:
        return ""
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        return soup.get_text(separator=" ").strip()
    return text


def load_profile() -> dict:
    """Parse profile.md into structured data."""
    if not PROFILE_FILE.exists():
        return {}

    content = PROFILE_FILE.read_text(encoding="utf-8")
    profile = {"raw": content}

    sections = re.split(r"^## ", content, flags=re.MULTILINE)
    for section in sections[1:]:
        lines = section.strip().split("\n")
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        profile[title.lower().replace(" ", "_")] = body

    return profile


def load_audience_insights() -> dict:
    """Load audience analysis data from connections scrape."""
    if not AUDIENCE_FILE.exists():
        return {}
    with open(AUDIENCE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Article Selection ───────────────────────────────────────────────────────

def pick_best_topic(articles: list[dict] = None, profile: dict = None) -> dict:
    """Select the best article using the priority system:
    1. Today's liked articles (from discovery swipes)
    2. Tie-break by audience alignment
    3. Fallback to highest-scored un-swiped cards
    4. Final fallback to raw scraped articles
    """
    import database as db

    audience = load_audience_insights()

    # Priority 1: Today's liked articles
    liked_today = db.get_today_liked_cards()
    if liked_today:
        print(f"[Generator] Found {len(liked_today)} liked articles from today.")
        if len(liked_today) == 1:
            return _card_to_article(liked_today[0])
        best = _rank_by_audience(liked_today, audience)
        return _card_to_article(best)

    # Fallback: highest-scored un-swiped cards
    pending_today = db.get_today_pending_cards()
    if pending_today:
        print("[Generator] No likes today. Using top-scored pending card.")
        best = _rank_by_audience(pending_today, audience)
        return _card_to_article(best)

    # Final fallback: raw articles (no discovery data)
    if articles:
        print("[Generator] No discovery data. Falling back to raw articles.")
        return _pick_from_raw(articles, profile or {}, audience)

    return {}


def _card_to_article(card: dict) -> dict:
    return {
        "title": card.get("title", ""),
        "summary": card.get("summary", ""),
        "link": card.get("url", ""),
        "source": card.get("source", ""),
        "type": card.get("type", "rss"),
    }


def _rank_by_audience(cards: list[dict], audience: dict) -> dict:
    """Rank cards by audience alignment, return single best."""
    breakdown = audience.get("breakdown", {})
    audience_keywords = [t["word"] for t in audience.get("top_titles", [])[:15]]

    scored = []
    for card in cards:
        score = card.get("preference_score", 0)
        title_lower = card.get("title", "").lower()
        summary_lower = strip_html(card.get("summary", "")).lower()
        combined = f"{title_lower} {summary_lower}"

        for aud_kw in audience_keywords:
            if aud_kw in combined:
                score += 2

        for cat, info in breakdown.items():
            if cat == "other":
                continue
            pct = info.get("percentage", 0)
            weight = max(1.0, pct ** 0.5)
            for headline in info.get("sample_headlines", []):
                for w in re.findall(r"\w+", strip_html(headline).lower()):
                    if len(w) > 3 and w in combined:
                        score += weight * 0.5
                        break

        scored.append((score, card))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _pick_from_raw(articles: list[dict], profile: dict, audience: dict) -> dict:
    """Final fallback: pick from raw scraped articles."""
    audience_keywords = [t["word"] for t in audience.get("top_titles", [])[:15]]
    interests_text = profile.get("my_interests", "").lower()

    scored = []
    for article in articles:
        score = 0
        title_lower = article["title"].lower()
        summary_lower = strip_html(article.get("summary", "")).lower()
        combined = f"{title_lower} {summary_lower}"

        for keyword in re.findall(r"[\w]+", interests_text):
            if len(keyword) > 3:
                if keyword in title_lower:
                    score += 3
                if keyword in summary_lower:
                    score += 1

        for aud_kw in audience_keywords:
            if aud_kw in combined:
                score += 2

        if article.get("type") == "gmail":
            score += 2
        if article.get("type") == "web_search":
            score += 1

        scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else articles[0]


# ─── Claude Code CLI integration ─────────────────────────────────────────────

def _claude_cli_available() -> bool:
    """Check if the claude CLI is installed and accessible."""
    return shutil.which("claude") is not None


def _build_audience_summary() -> str:
    audience = load_audience_insights()
    if not audience.get("breakdown"):
        return "(No audience data scraped yet.)"

    top_cats = sorted(
        [(cat, info.get("percentage", 0))
         for cat, info in audience["breakdown"].items() if cat != "other"],
        key=lambda x: x[1], reverse=True
    )[:5]

    summary = "Connections breakdown: " + ", ".join(
        f"{cat} ({pct}%)" for cat, pct in top_cats
    )

    top_kw = audience.get("top_titles", [])[:10]
    if top_kw:
        summary += "\nTop topics your connections post about: "
        summary += ", ".join(f"{t['word']} ({t['count']}x)" for t in top_kw)

    return summary


def _build_rejection_feedback() -> str:
    """Pull last 5 rejection reasons from DB; format as bullets."""
    import database as db
    reasons = db.get_recent_rejection_reasons(limit=5)
    if not reasons:
        return "(No previous rejection feedback yet — first run or no rejections.)"
    return "\n".join(f"- {r}" for r in reasons)


def _build_audience_hook_segments() -> str:
    """Build detailed audience segment info for the audience_hook post type.
    Includes segment names, percentages, and sample headlines for targeting."""
    audience = load_audience_insights()
    if not audience.get("breakdown"):
        return "(No audience data. Use generic developer hook.)"

    lines = []
    breakdown = audience["breakdown"]
    for cat, info in sorted(breakdown.items(), key=lambda x: x[1].get("count", 0), reverse=True):
        if cat == "other":
            continue
        pct = info.get("percentage", 0)
        count = info.get("count", 0)
        label = cat.replace("_", " ").title()
        headlines = [h for h in info.get("sample_headlines", []) if h.strip()][:3]
        headline_str = "; ".join(headlines) if headlines else "N/A"
        lines.append(f"- {label} ({pct}%, {count} people): {headline_str}")

    # Top keywords for context
    top_kw = audience.get("top_titles", [])[:10]
    if top_kw:
        lines.append(f"\nTop network keywords: {', '.join(t['word'] for t in top_kw)}")

    return "\n".join(lines) if lines else "(No segment data available.)"


def _fetch_article_body(article: dict) -> str:
    """Fetch full article text from the URL using trafilatura.
    This gives Claude real substance to form opinions about,
    instead of just a 2-sentence RSS summary."""
    url = article.get("link", "")
    if not url or not url.startswith("http"):
        return ""
    # Skip Gmail links — they're not real articles
    if "mail.google.com" in url:
        return ""
    source = article.get("source", "article")
    _step("article", f"Reading full article from {source}...")
    try:
        from news_scraper import fetch_full_article
        body = fetch_full_article(url, timeout=15)
        if body and len(body) > 100:
            _step("article", f"Extracted {len(body)} chars of article content")
            return body
        _step("article", "Article text too short, using summary")
    except Exception as e:
        _step("article", f"Could not fetch article: {str(e)[:50]}")
    return ""


def _build_post_context(article: dict, post_type: str, draft_id: str,
                        output_path: Path) -> Path:
    """Fill the template with article + profile + audience + feedback.
    Fetches full article text so Claude has real content to work from.
    Writes to _post_context.md and returns the path."""
    if not CONTEXT_TEMPLATE.exists():
        raise FileNotFoundError(
            f"Context template missing: {CONTEXT_TEMPLATE}. "
            "Run the install / restore _post_context_template.md."
        )

    template = CONTEXT_TEMPLATE.read_text(encoding="utf-8")

    _step("profile", "Reading your voice profile and past posts...")
    profile_md = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""

    article_clean = {
        "title": article.get("title", ""),
        "summary": strip_html(article.get("summary", "")),
        "link": article.get("link", ""),
        "source": article.get("source", ""),
        "type": article.get("type", "rss"),
    }

    # ─── Fetch full article text for real substance ────────────────────
    full_body = _fetch_article_body(article)

    _step("audience", "Analyzing your audience connections...")

    replacements = {
        "{{POST_TYPE}}": post_type,
        "{{ARTICLE_TITLE}}": article_clean["title"],
        "{{ARTICLE_SOURCE}}": article_clean["source"],
        "{{ARTICLE_LINK}}": article_clean["link"],
        "{{ARTICLE_SUMMARY}}": article_clean["summary"][:2000],
        "{{ARTICLE_FULL_TEXT}}": full_body[:3000] if full_body else "(Could not fetch full article. Use the summary above.)",
        "{{ARTICLE_JSON}}": json.dumps(article_clean, ensure_ascii=False),
        "{{PROFILE_MD}}": profile_md,
        "{{AUDIENCE_SUMMARY}}": _build_audience_summary(),
        "{{AUDIENCE_HOOK_SEGMENTS}}": _build_audience_hook_segments() if post_type == "audience_hook" else "(Not applicable for this post type.)",
        "{{REJECTION_REASONS}}": _build_rejection_feedback(),
        "{{OUTPUT_PATH}}": str(output_path).replace("\\", "/"),
        "{{DRAFT_ID}}": draft_id,
        "{{CREATED_AT}}": datetime.now().isoformat(),
    }

    filled = template
    for key, val in replacements.items():
        filled = filled.replace(key, val)

    CONTEXT_FILE.write_text(filled, encoding="utf-8")
    return CONTEXT_FILE


def _run_claude_code(output_path: Path, post_type: str) -> bool:
    """Invoke `claude -p` to generate the post. Returns True on success.
    Sends the prompt via stdin (more reliable than positional arg on Windows)."""
    from config import get_setting

    if not _claude_cli_available():
        _step("error", "Claude CLI not found on PATH")
        return False

    # If gmail_method is claude_mcp, tell Claude it can search Gmail for newsletters
    gmail_hint = ""
    gmail_method = get_setting("gmail_method", "none")
    if gmail_method == "claude_mcp":
        from config import get_content_topic
        topic = get_content_topic()
        _step("gmail", "Connecting to Gmail for newsletter content...")
        gmail_hint = (
            " You also have access to the user's Gmail via MCP. "
            f"Search Gmail for {topic} newsletters from the past week related to the article topic. "
            f"Use the Gmail search tool with queries like '{topic} newsletter' or the article topic. "
            "Include any relevant insights from newsletters you find."
        )

    prompt = (
        f"Read _post_context.md (in the current directory) and follow its "
        f"instructions to generate a LinkedIn post. The post type is "
        f"'{post_type}'. Read my_past_posts.md for voice calibration. "
        f"Save the final draft as valid JSON to {str(output_path).replace(chr(92), '/')}. "
        f"Use the Write tool. Do not print the post text to stdout - only write the file."
        f"{gmail_hint}"
    )

    allowed_tools = ["Read", "Write", "Edit"]
    if gmail_method == "claude_mcp":
        # Permit the Gmail MCP tools so Claude can actually act on the hint above;
        # mirrors approval_server's interactive-generation path.
        allowed_tools += [
            "mcp__claude_ai_Gmail",
            "mcp__claude_ai_Gmail__search_threads",
            "mcp__claude_ai_Gmail__get_thread",
            "mcp__claude_ai_Gmail__list_threads",
            "mcp__claude_ai_Gmail__list_labels",
        ]

    cmd = [
        "claude", "-p",
        "--allowed-tools", ",".join(allowed_tools),
        "--add-dir", str(BASE_DIR),
    ]

    _step("claude", "Claude is writing your post...")
    try:
        result = subprocess.run(
            cmd,
            input=prompt,  # Pipe via stdin — more reliable on Windows
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            cwd=str(BASE_DIR),
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(f"[Generator] Claude CLI timed out after {CLAUDE_TIMEOUT_SECONDS}s.")
        return False
    except FileNotFoundError:
        print("[Generator] Claude CLI binary not found.")
        return False
    except Exception as e:
        print(f"[Generator] Claude CLI subprocess error: {e}")
        return False

    if result.returncode != 0:
        print(f"[Generator] Claude CLI exited with code {result.returncode}")
        if result.stderr:
            print(f"[Generator] stderr: {result.stderr[:500]}")
        if result.stdout:
            print(f"[Generator] stdout: {result.stdout[:500]}")
        return False

    if not output_path.exists():
        print(f"[Generator] Claude finished but no draft file at {output_path}")
        if result.stdout:
            print(f"[Generator] stdout preview: {result.stdout[:1000]}")
        return False

    print(f"[Generator] Claude wrote draft: {output_path.name}")
    return True


# ─── Post Draft Creation ─────────────────────────────────────────────────────

def generate_post_draft(article: dict, profile: dict = None,
                        post_type: str = "news") -> dict:
    """Generate a LinkedIn post draft using Claude Code CLI.
    Falls back to a smart template if Claude CLI fails."""
    profile = profile or load_profile()
    draft_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = POSTS_DIR / f"draft_{draft_id}.json"

    # Build the context.md that Claude will read
    _build_post_context(article, post_type, draft_id, output_path)

    # Try Claude Code CLI
    if _run_claude_code(output_path, post_type):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                draft = json.load(f)
            # Validate required fields, fill gaps
            draft.setdefault("id", draft_id)
            draft.setdefault("status", "pending_approval")
            draft.setdefault("article", article)
            draft.setdefault("generated_by", "claude_code_cli")
            draft.setdefault("created_at", datetime.now().isoformat())
            draft.setdefault("post_type", post_type)

            # Image URLs not yet populated — fill if needs_image
            if draft.get("needs_image") and not draft.get("image_urls"):
                if draft.get("image_query"):
                    draft["image_urls"] = get_image_urls(draft["image_query"])

            # Re-save with our validated structure
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(draft, f, indent=2, ensure_ascii=False)

            print(f"[Generator] Draft ready: {output_path.name}")
            return draft

        except (json.JSONDecodeError, IOError) as e:
            print(f"[Generator] Failed to parse Claude's draft: {e}")
            # Fall through to template fallback

    # ─── Fallback: smart template ────────────────────────────────────────
    print("[Generator] Falling back to smart template generation.")
    return _generate_template_draft(article, profile, post_type, draft_id, output_path)


def _generate_template_draft(article: dict, profile: dict, post_type: str,
                              draft_id: str, output_path: Path) -> dict:
    """Smart template fallback — much better than the old 'Worth watching' stub.
    Used when Claude CLI is unavailable."""
    title = article.get("title", "Untitled")
    summary = strip_html(article.get("summary", ""))
    source = article.get("source", "")
    link = article.get("link", "")

    # Trim summary to clean sentence ending
    facts = summary[:280].strip()
    if "." in facts:
        facts = facts.rsplit(".", 1)[0] + "."

    # Build a hook based on post type
    if post_type == "audience_hook":
        # Pick the largest non-other segment for targeting
        audience = load_audience_insights()
        segment = "devs"
        breakdown = audience.get("breakdown", {})
        top_cat = max(
            ((k, v.get("count", 0)) for k, v in breakdown.items() if k != "other"),
            key=lambda x: x[1], default=("developers", 0)
        )[0]
        segment_labels = {
            "developers": "Frontend devs",
            "designers": "Designers",
            "recruiters": "HR folks",
            "founders_ceos": "Founders",
            "managers": "Team leads",
            "students": "CS students",
            "data_ai": "Data/AI people",
        }
        segment = segment_labels.get(top_cat, "Devs")
        hook = f"{segment}: how does this change your daily workflow?"
        body = facts
        take = "Curious what this looks like from your side of the desk."
    elif post_type == "builder":
        hook = "Quick thing I noticed while building this week."
        body = facts
        take = "If you're shipping AI tools too, this is one of those details that matters more than it looks."
    elif post_type == "hot_take":
        hook = title.rstrip(".") + "."
        body = facts
        take = "I think most people are underestimating what this means for devs."
    elif post_type == "research":
        hook = f"New paper / writeup worth reading from {source}."
        body = facts
        take = "The interesting part is what this implies for production systems, not just benchmarks."
    else:  # news
        hook = "Saw this and wanted to break it down."
        body = facts
        take = "From a dev perspective, this shifts what the standard stack looks like."

    cta = "What's your take?"
    hashtags = generate_hashtags(title, 3)

    parts = [hook, body, take, cta]
    if link:
        parts.append(link)
    if hashtags:
        parts.append(hashtags)

    post_content = "\n\n".join(parts)

    # Try humanizer if available
    try:
        from humanizer import humanize_text
        post_content = humanize_text(post_content)
    except Exception:
        pass

    needs_image = determine_needs_image(article)
    image_query = suggest_image_query(article) if needs_image else None
    image_urls = get_image_urls(image_query) if image_query else None

    draft = {
        "id": draft_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending_approval",
        "article": article,
        "post_content": post_content,
        "post_type": post_type,
        "style_used": "template",
        "needs_image": needs_image,
        "image_query": image_query,
        "image_urls": image_urls,
        "image_path": None,
        "generated_by": "template_fallback",
        "profile_used": True,
        "hashtags": hashtags.split() if hashtags else [],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)

    print(f"[Generator] Template draft saved: {output_path.name}")
    return draft


# ─── Helpers (image suggestions, hashtags) ───────────────────────────────────

def determine_needs_image(article: dict) -> bool:
    visual_keywords = [
        "robot", "chip", "hardware", "product", "launch", "demo",
        "interface", "design", "chart", "data", "graph", "visualization"
    ]
    text_only_keywords = [
        "opinion", "regulation", "policy", "ethics", "funding",
        "acquisition", "partnership", "hire", "leadership"
    ]

    combined = (article.get("title", "") + " " + article.get("summary", "")).lower()
    visual_score = sum(1 for kw in visual_keywords if kw in combined)
    text_score = sum(1 for kw in text_only_keywords if kw in combined)
    return visual_score > text_score


def suggest_image_query(article: dict) -> str:
    title = article.get("title", "")
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
                  "to", "for", "of", "and", "or", "but", "with", "from", "by", "as",
                  "that", "this", "it"}
    words = [w.lower() for w in re.findall(r"\w+", title)
             if w.lower() not in stop_words and len(w) > 2]
    return " ".join(words[:3]) + " technology"


def get_image_urls(query: str) -> dict:
    return {
        name: url.format(query=query.replace(" ", "-"))
        for name, url in IMAGE_SOURCES.items()
    }


def generate_hashtags(title: str, max_count: int = 3) -> str:
    ai_hashtags = {
        "ai": "#AI", "artificial intelligence": "#AI",
        "machine learning": "#MachineLearning", "llm": "#LLMs",
        "gpt": "#GPT", "claude": "#Claude", "openai": "#OpenAI",
        "anthropic": "#Anthropic", "google": "#GoogleAI",
        "robot": "#Robotics", "chip": "#AIChips",
        "startup": "#AIStartups", "open source": "#OpenSource",
        "model": "#AIModels", "agent": "#AIAgents",
        "mcp": "#MCP", "react": "#ReactJS", "next": "#NextJS",
        "frontend": "#Frontend", "developer": "#WebDev",
    }

    title_lower = title.lower()
    matched = []
    for keyword, hashtag in ai_hashtags.items():
        if keyword in title_lower and hashtag not in matched:
            matched.append(hashtag)
        if len(matched) >= max_count:
            break

    if not matched:
        matched = ["#AI", "#WebDev"]

    return " ".join(matched[:max_count])


if __name__ == "__main__":
    test_article = {
        "title": "Anthropic launches Claude 4.5 with improved reasoning",
        "summary": "Anthropic has released Claude 4.5, featuring significant improvements in mathematical reasoning and code generation. The model shows 30% improvement on benchmarks.",
        "link": "https://example.com/article",
        "source": "TechCrunch",
        "type": "rss"
    }
    profile = load_profile()
    draft = generate_post_draft(test_article, profile, post_type="news")
    print("\n=== GENERATED POST ===")
    print(draft["post_content"])
    print(f"\nGenerated by: {draft.get('generated_by', 'unknown')}")
