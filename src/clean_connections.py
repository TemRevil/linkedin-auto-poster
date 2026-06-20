"""
Connection Data Cleaner + Per-Profile Scraper
==============================================
Two modes:
1. clean  — Parse existing connections.json, fix garbage data using regex
2. scrape — Visit each connection's LinkedIn profile page for accurate data

Usage:
    python clean_connections.py clean         # Fix existing data offline
    python clean_connections.py scrape        # Rescrape from profile pages (Playwright)
    python clean_connections.py scrape --max 50  # Scrape first 50 only
"""
import json
import re
import sys
import asyncio
from pathlib import Path
from datetime import datetime

from config import BASE_DIR, AUTH_DIR, SESSIONS_FILE

CONNECTIONS_FILE = BASE_DIR / "connections.json"
BACKUP_FILE = BASE_DIR / "connections_backup.json"
CLEAN_FILE = BASE_DIR / "connections_clean.json"

# Arabic patterns found in the garbage data
ARABIC_DEGREE = r"\s*[•·]\s*من الدرجة الأولى"
ARABIC_MESSAGE = r"رسالة"
ARABIC_MUTUAL = r"[\w\s؀-ۿ\.\-']+ و[\w\s؀-ۿ\.\-']+ و?[٠-٩\d]* ?زميل[ا ]* ?(واحد )?آخر مشترك"
ARABIC_MUTUAL_SIMPLE = r"[\w\s؀-ۿ\.\-]+ زميل[ان]* مشترك[ان]*"
# Location pattern: Arabic/city text before "رسالة"
LOCATION_PATTERN_AR = r"([؀-ۿ\w\s,\-]+(?:مصر|تركيا|صربيا|الأردن|السعودية|الإمارات|الكويت|قطر|المغرب|تونس|الجزائر|العراق|لبنان|فلسطين))"


def clean_name(raw_name: str) -> str:
    """Extract just the person's real name from a blob."""
    if not raw_name:
        return ""

    # If name contains "•" or Arabic degree marker, split there
    name = re.split(r"\s*[•·]\s*", raw_name)[0].strip()

    # Remove "من الدرجة الأولى" if it snuck in
    name = re.sub(r"من الدرجة الأولى.*", "", name).strip()

    # Remove any trailing Arabic text that isn't part of a name
    # (but keep Arabic names — they typically are short)
    # Remove "رسالة" and everything after
    name = re.split(ARABIC_MESSAGE, name)[0].strip()

    # Remove mutual connection text patterns
    name = re.sub(ARABIC_MUTUAL, "", name).strip()
    name = re.sub(ARABIC_MUTUAL_SIMPLE, "", name).strip()

    # If what remains still has a headline embedded (long text after the actual name)
    # Heuristic: names are usually 2-4 words; if > 6 words and contains job keywords, truncate
    words = name.split()
    if len(words) > 5:
        # Check if there's a job title embedded (common patterns)
        job_markers = [
            "developer", "engineer", "designer", "manager", "founder",
            "student", "intern", "ceo", "cto", "lead", "senior",
            "junior", "software", "frontend", "backend", "fullstack",
            "data", "analyst", "recruiter", "hr", "marketing",
            "product", "director", "vp", "head", "consultant",
            "freelance", "instructor", "professor", "teacher",
            "specialist", "coordinator", "supervisor", "officer",
            "at ", "| ", "- ",
        ]
        lower_name = name.lower()
        for marker in job_markers:
            idx = lower_name.find(marker)
            if idx > 0:
                # Truncate at the job marker, keeping only what's before
                # But make sure we keep at least 2 words (the actual name)
                candidate = name[:idx].strip()
                if len(candidate.split()) >= 2:
                    name = candidate.rstrip("|-– ")
                    break

    # Final cleanup: remove trailing punctuation and degree text
    name = name.strip(" •·|-–")
    name = re.sub(r"\s+", " ", name)

    return name


def clean_headline(raw_headline: str, raw_name: str, raw_location: str) -> str:
    """Extract a proper headline from the messy data."""
    if not raw_headline:
        # Maybe the headline is in the location field (common swap)
        if raw_location and not is_junk_location(raw_location):
            if looks_like_headline(raw_location):
                return raw_location
        return ""

    # If headline is just the person's name (or name + mutual text), it's useless
    clean_n = clean_name(raw_name)
    if raw_headline.startswith(clean_n) or raw_headline == raw_name:
        # Headline is just repeated name + degree marker — discard
        return ""

    # If headline contains mutual connection text, it's garbage
    if re.search(r"زميل.*مشترك", raw_headline):
        return ""
    if " و" in raw_headline and re.search(r"[٠-٩\d]", raw_headline):
        return ""

    # Remove "• من الدرجة الأولى" from headline
    headline = re.sub(ARABIC_DEGREE, "", raw_headline).strip()
    # Remove name prefix if present
    if headline.startswith(clean_n):
        headline = headline[len(clean_n):].strip(" •·|-–")

    return headline.strip()


def clean_location(raw_location: str, raw_name: str) -> str:
    """Extract proper location from the messy data."""
    if not raw_location:
        return ""

    # If location is just Arabic degree marker, discard
    if re.match(r"^[•·]?\s*من الدرجة الأولى\s*$", raw_location):
        return ""

    # If location looks like a headline/job title (common swap), return empty
    if looks_like_headline(raw_location):
        return ""

    # Remove Arabic text that isn't location
    loc = re.sub(r"من الدرجة الأولى", "", raw_location).strip()
    loc = re.sub(r"^[•·]\s*", "", loc).strip()

    # If it's a proper location (city, country pattern), keep it
    if loc and not looks_like_headline(loc):
        return loc

    return ""


def is_junk_location(text: str) -> bool:
    """Check if location text is garbage."""
    junk_patterns = [
        r"^[•·]?\s*من الدرجة",
        r"زميل.*مشترك",
        r"رسالة",
    ]
    return any(re.search(p, text) for p in junk_patterns)


def looks_like_headline(text: str) -> bool:
    """Check if text looks like a job headline rather than location."""
    headline_markers = [
        "developer", "engineer", "designer", "manager", "founder",
        "student", "intern", "ceo", "cto", "lead", "senior",
        "software", "frontend", "backend", "fullstack", "data",
        "analyst", "recruiter", "hr", "marketing", "product",
        "director", "head of", "specialist", "coordinator",
        "supervisor", "officer", "freelance", "consultant",
        "at ", "| ", "aspiring", "passionate",
    ]
    lower = text.lower()
    return any(marker in lower for marker in headline_markers)


def extract_headline_from_name_blob(raw_name: str) -> str:
    """Try to extract a headline from the name blob (between degree marker and location/message)."""
    # Pattern: "Name • من الدرجة الأولىHEADLINELocationرسالة..."
    match = re.search(r"من الدرجة الأولى(.+?)(?:رسالة|$)", raw_name)
    if match:
        blob = match.group(1).strip()
        # The blob contains "HeadlineLocation" concatenated
        # Try to separate: headlines usually have English job keywords
        # Locations are typically "City Country" patterns

        # Look for Arabic location patterns at the end
        loc_match = re.search(
            r"((?:[؀-ۿ]+\s+){1,4}(?:مصر|تركيا|صربيا|الأردن|السعودية|الإمارات|الكويت|قطر|المغرب|تونس|الجزائر|العراق|لبنان|فلسطين|ألمانيا|فرنسا|بريطانيا|كندا|أمريكا))\s*$",
            blob,
        )
        if loc_match:
            headline = blob[: loc_match.start()].strip()
        else:
            # Try to find where location starts (city names pattern)
            # Cities in English
            city_match = re.search(
                r"((?:Cairo|Egypt|Turkey|Serbia|Jordan|Saudi|UAE|Dubai|Qatar|Morocco|Tunisia|Germany|France|UK|Canada|USA|United States|India|Pakistan|Remote).*?)$",
                blob, re.IGNORECASE
            )
            if city_match:
                headline = blob[: city_match.start()].strip()
            else:
                headline = blob

        # Remove mutual connection text from end
        headline = re.split(r"[\w\s]+ و[\w\s]+", headline)[0].strip()
        return headline

    return ""


def extract_location_from_name_blob(raw_name: str) -> str:
    """Try to extract location from the name blob."""
    # Look for Arabic location patterns
    match = re.search(
        r"((?:[؀-ۿ\w\s]+\s+)?(?:مصر|تركيا|صربيا|الأردن|السعودية|الإمارات|الكويت|قطر|المغرب|تونس|الجزائر|العراق|لبنان|فلسطين))(?:\s*رسالة)?",
        raw_name,
    )
    if match:
        loc = match.group(1).strip()
        # Clean up: remove "القاهرة القاهرة" duplicates, keep just key parts
        parts = loc.split()
        # Deduplicate consecutive words
        cleaned = []
        for p in parts:
            if not cleaned or p != cleaned[-1]:
                cleaned.append(p)
        return " ".join(cleaned)

    # Try English locations
    match = re.search(
        r"((?:Cairo|Alexandria|Giza|Istanbul|Ankara|Belgrade|Amman|Dubai|Riyadh|Doha|Casablanca|Tunis|Berlin|Paris|London|Toronto|Remote)[\w\s,]*?)(?:رسالة|$)",
        raw_name, re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    return ""


def clean_connections_data():
    """Clean existing connections.json data offline using regex parsing."""
    if not CONNECTIONS_FILE.exists():
        print("[Clean] connections.json not found!")
        return

    with open(CONNECTIONS_FILE, "r", encoding="utf-8") as f:
        connections = json.load(f)

    print(f"[Clean] Loaded {len(connections)} connections")

    # Backup original (atomic: temp file + replace)
    backup_tmp = BACKUP_FILE.with_suffix(".tmp")
    with open(backup_tmp, "w", encoding="utf-8") as f:
        json.dump(connections, f, indent=2, ensure_ascii=False)
    backup_tmp.replace(BACKUP_FILE)
    print(f"[Clean] Backup saved to {BACKUP_FILE.name}")

    cleaned = []
    fixed_count = 0
    skipped = 0

    seen_urls = set()

    for conn in connections:
        raw_name = conn.get("name", "")
        raw_headline = conn.get("headline", "")
        raw_location = conn.get("location", "")
        profile_url = conn.get("profile_url", "")

        # Skip duplicates
        if profile_url in seen_urls:
            skipped += 1
            continue
        seen_urls.add(profile_url)

        # Clean name
        name = clean_name(raw_name)
        if not name or len(name) < 2:
            skipped += 1
            continue

        # Clean headline
        headline = clean_headline(raw_headline, raw_name, raw_location)

        # If headline is empty but name blob has data, try to extract
        if not headline:
            headline = extract_headline_from_name_blob(raw_name)
            # Also check if location field actually has the headline
            if not headline and raw_location and looks_like_headline(raw_location):
                headline = raw_location

        # Clean location
        location = clean_location(raw_location, raw_name)
        # If location is empty, try extracting from name blob
        if not location:
            location = extract_location_from_name_blob(raw_name)

        # Track if we made changes
        if name != raw_name or headline != raw_headline or location != raw_location:
            fixed_count += 1

        cleaned.append({
            "name": name,
            "headline": headline,
            "location": location,
            "profile_url": profile_url,
        })

    # Second pass: clean Arabic text from headlines and fix locations
    for conn in cleaned:
        headline = conn.get("headline", "")
        location = conn.get("location", "")

        # Strip Arabic text from end of English headlines
        # Pattern: "Senior Software Engineerصربيا..." -> "Senior Software Engineer"
        if headline:
            # Find where Arabic starts (if headline is mostly English)
            match = re.search(r"[؀-ۿ]", headline)
            if match:
                # Only strip if there's substantial English before the Arabic
                before = headline[:match.start()].strip()
                if len(before) > 5 and any(c.isascii() and c.isalpha() for c in before):
                    conn["headline"] = before.rstrip(" |/-–•·")

        # Fix location: remove "من الدرجة الأولى" prefix and headline text
        location = conn.get("location", "")
        if location:
            # Remove degree marker prefix
            location = re.sub(r"^من الدرجة الأولى\s*", "", location)
            # Remove headline text that leaked into location
            h = conn.get("headline", "")
            if h and location.startswith(h):
                location = location[len(h):].strip()
            # If location starts with last word of headline (partial overlap)
            if h:
                last_word = h.split()[-1] if h.split() else ""
                if last_word and location.startswith(last_word):
                    location = location[len(last_word):].strip()

            # Extract just Arabic location (city + country) or English location
            ar_loc = re.search(
                r"((?:[؀-ۿ]+[\s,]*){1,4}(?:مصر|تركيا|صربيا|الأردن|السعودية|الإمارات|الكويت|قطر|المغرب|تونس|الجزائر|العراق|لبنان|فلسطين|ألمانيا|بريطانيا|كندا))",
                location,
            )
            en_loc = re.search(
                r"((?:Cairo|Giza|Alexandria|Istanbul|Ankara|Belgrade|Amman|Dubai|Riyadh|Doha|Remote|Egypt|Turkey|Serbia|Jordan|Saudi|UAE)[\w\s,]*)",
                location, re.IGNORECASE,
            )

            if ar_loc:
                conn["location"] = ar_loc.group(1).strip()
            elif en_loc:
                conn["location"] = en_loc.group(1).strip()
            elif looks_like_headline(location):
                # Location field has a job title — clear it
                conn["location"] = ""
            else:
                conn["location"] = location.strip()

    # Save cleaned data (atomic: temp file + replace)
    tmp = CONNECTIONS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)
    tmp.replace(CONNECTIONS_FILE)

    print(f"[Clean] Done! {len(cleaned)} connections saved ({fixed_count} fixed, {skipped} duplicates removed)")
    print(f"[Clean] Original backed up at: {BACKUP_FILE.name}")


async def scrape_profiles(max_count: int = 0):
    """
    Visit each connection's LinkedIn profile page via Playwright
    and extract clean name/headline/location data.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[Scrape] playwright not installed. Run: pip install playwright && playwright install")
        return

    if not CONNECTIONS_FILE.exists():
        print("[Scrape] connections.json not found!")
        return

    with open(CONNECTIONS_FILE, "r", encoding="utf-8") as f:
        connections = json.load(f)

    # Get session state for authenticated browsing
    session_file = _get_linkedin_session_file()
    if not session_file or not session_file.exists():
        print("[Scrape] No LinkedIn session found. Login first via the dashboard.")
        return

    total = len(connections)
    if max_count > 0:
        total = min(total, max_count)

    print(f"[Scrape] Will visit {total} profiles using session: {session_file.name}")
    print("[Scrape] This will take a while (1-3 sec per profile to avoid rate-limiting)...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            storage_state=str(session_file),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        # Set a realistic user agent
        await page.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
        })

        updated = 0
        errors = 0

        try:
          for i, conn in enumerate(connections[:total]):
            url = conn.get("profile_url", "")
            if not url:
                continue

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1500)  # Let profile load

                # Extract data from profile page (name, headline, location + recent posts)
                profile_data = await page.evaluate("""() => {
                    const result = {name: '', headline: '', location: '', recent_posts: []};

                    // Name: h1 with the profile name
                    const nameEl = document.querySelector('h1.text-heading-xlarge') ||
                                   document.querySelector('h1[class*="break-words"]') ||
                                   document.querySelector('.pv-top-card h1');
                    if (nameEl) result.name = nameEl.innerText.trim();

                    // Headline: the text below the name
                    const headlineEl = document.querySelector('div.text-body-medium.break-words') ||
                                       document.querySelector('.pv-top-card--list .text-body-medium') ||
                                       document.querySelector('[data-generated-suggestion-target="urn:li:fsu_profileActionDelegate"]');
                    if (headlineEl) result.headline = headlineEl.innerText.trim();

                    // Location: usually in a span with specific classes
                    const locEl = document.querySelector('span.text-body-small.inline.t-black--light.break-words') ||
                                  document.querySelector('.pv-top-card--list-bullet .text-body-small') ||
                                  document.querySelector('[class*="top-card"] .text-body-small');
                    if (locEl) result.location = locEl.innerText.trim();

                    // Recent activity/posts — extract text from the Activity section
                    const activityItems = document.querySelectorAll(
                        '.pv-recent-activity-section__feed-item, ' +
                        '[class*="feed-shared-update"], ' +
                        '[class*="profile-creator-shared-feed"] .feed-shared-text, ' +
                        '.pv-shared-text-with-more span[dir], ' +
                        '[data-test-id="main-feed-activity-content"] span'
                    );
                    activityItems.forEach(item => {
                        const text = (item.innerText || '').trim();
                        if (text.length > 20 && text.length < 2000) {
                            result.recent_posts.push(text.substring(0, 500));
                        }
                    });

                    // Fallback: grab any visible text from activity cards
                    if (result.recent_posts.length === 0) {
                        const cards = document.querySelectorAll(
                            '[class*="activity"] [class*="update-components-text"], ' +
                            '[class*="activity"] .break-words span[dir="ltr"]'
                        );
                        cards.forEach(card => {
                            const text = (card.innerText || '').trim();
                            if (text.length > 20 && text.length < 2000) {
                                result.recent_posts.push(text.substring(0, 500));
                            }
                        });
                    }

                    return result;
                }""")

                # Update connection if we got data
                if profile_data.get("name"):
                    conn["name"] = profile_data["name"]
                if profile_data.get("headline"):
                    conn["headline"] = profile_data["headline"]
                if profile_data.get("location"):
                    conn["location"] = profile_data["location"]
                if profile_data.get("recent_posts"):
                    conn["recent_posts"] = profile_data["recent_posts"]

                updated += 1
                if (i + 1) % 10 == 0:
                    print(f"  [{i+1}/{total}] {conn['name']} — {conn.get('headline', '')[:40]}")

                # Rate limiting: random delay 1.5-3s
                import random
                await page.wait_for_timeout(random.randint(1500, 3000))

            except Exception as e:
                errors += 1
                if errors > 20:
                    print(f"[Scrape] Too many errors ({errors}), stopping. Last: {e}")
                    break
                if "net::ERR" in str(e) or "429" in str(e):
                    print(f"  [{i+1}] Rate limited, waiting 30s...")
                    await page.wait_for_timeout(30000)
                continue

            # Save progress every 25 profiles
            if (i + 1) % 25 == 0:
                tmp = CONNECTIONS_FILE.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(connections, f, indent=2, ensure_ascii=False)
                tmp.replace(CONNECTIONS_FILE)
                print(f"  [Progress saved: {i+1}/{total}]")
        finally:
            await browser.close()

    # Final save (atomic: temp file + replace)
    tmp = CONNECTIONS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(connections, f, indent=2, ensure_ascii=False)
    tmp.replace(CONNECTIONS_FILE)

    print(f"\n[Scrape] Done! Updated {updated} profiles ({errors} errors)")


def _get_linkedin_session_file() -> Path | None:
    """Get the active LinkedIn session file."""
    if SESSIONS_FILE.exists():
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        for s in config.get("linkedin", []):
            if s.get("active"):
                return AUTH_DIR / s["file"]
    # Fallback to legacy file
    legacy = AUTH_DIR / "linkedin.json"
    if legacy.exists():
        return legacy
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python clean_connections.py [clean|scrape] [--max N]")
        print("  clean  — Fix existing data using regex parsing")
        print("  scrape — Visit each profile page for accurate data (needs Playwright)")
        sys.exit(1)

    mode = sys.argv[1].lower()

    if mode == "clean":
        clean_connections_data()
    elif mode == "scrape":
        max_n = 0
        if "--max" in sys.argv:
            idx = sys.argv.index("--max")
            if idx + 1 < len(sys.argv):
                max_n = int(sys.argv[idx + 1])
        asyncio.run(scrape_profiles(max_n))
    else:
        print(f"Unknown mode: {mode}. Use 'clean' or 'scrape'.")
