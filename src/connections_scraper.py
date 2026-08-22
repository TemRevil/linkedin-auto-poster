"""
Scrapes LinkedIn connections data with proper field separation.
Extracts name, headline (job/title), and location as separate fields.
Uses patient scrolling to load all connections before extracting.
"""
import json
import asyncio
import sys
import re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

from config import AUTH_DIR, BASE_DIR

CONNECTIONS_FILE = BASE_DIR / "connections.toml"
CONNECTIONS_JSON = BASE_DIR / "connections.json"
CONNECTIONS_SUMMARY_FILE = BASE_DIR / "audience_insights.json"

# Robust JS extractor — separates name, headline, and location
EXTRACT_JS = """() => {
    const results = [];
    const seen = new Set();
    const clean = s => s.replace(/[\\u200f\\u200e\\u2066\\u2069\\u202a\\u202c\\u200b\\ufeff]/g, '').trim();
    const skipRx = /^(message|connect|follow|pending|1st|2nd|3rd|degree|mutual|\\d+ mutual|invite|withdraw|view profile|view|remove|send|more|accept|ignore|see all|\\.\\.\\.)$/i;
    const isJunk = t => !t || t.length < 2 || skipRx.test(t);

    // --- Method 1: LinkedIn structured search results ---
    const containers = document.querySelectorAll(
        'li.reusable-search__result-container, li[class*="search-result"], div[data-chameleon-result-urn]'
    );

    if (containers.length > 0) {
        containers.forEach(item => {
            const link = item.querySelector('a[href*="/in/"]');
            if (!link) return;

            const href = link.href.split('?')[0];
            if (seen.has(href) || href.includes('/in/me/') || href.includes('/in/ACoA')) return;

            // Name: LinkedIn wraps the actual name in span[aria-hidden="true"] inside the link
            let name = '';
            const ariaSpan = link.querySelector('span[aria-hidden="true"]');
            if (ariaSpan) {
                name = clean(ariaSpan.textContent);
            } else {
                const spans = Array.from(link.querySelectorAll('span'))
                    .map(s => clean(s.textContent))
                    .filter(s => s.length > 1 && s.length < 60 && !isJunk(s));
                if (spans.length) name = spans[0];
            }

            // Headline: primary subtitle element
            let headline = '';
            const primarySub = item.querySelector(
                '[class*="primary-subtitle"], [class*="primarySubtitle"], ' +
                '.entity-result__primary-subtitle'
            );
            if (primarySub) headline = clean(primarySub.textContent);

            // Location: secondary subtitle element
            let location = '';
            const secondarySub = item.querySelector(
                '[class*="secondary-subtitle"], [class*="secondarySubtitle"], ' +
                '.entity-result__secondary-subtitle'
            );
            if (secondarySub) location = clean(secondarySub.textContent);

            // Fallback: text-line parsing if selectors failed
            if (!headline && !location) {
                const lines = item.innerText.split('\\n')
                    .map(l => clean(l))
                    .filter(l => l.length > 1 && l !== name && !isJunk(l));

                const locationWords = [
                    'egypt','cairo','giza','alexandria','istanbul','ankara','london','dubai',
                    'riyadh','jeddah','amman','beirut','casablanca','rabat','tunis','algiers',
                    'paris','berlin','munich','new york','san francisco','los angeles','chicago',
                    'toronto','vancouver','sydney','melbourne','mumbai','delhi','bangalore',
                    'karachi','lahore','lagos','nairobi','accra','doha','kuwait','bahrain',
                    'area','region','metropolitan','governorate','province','state','county',
                    'united states','united kingdom','united arab','saudi arabia','deutschland'
                ];
                const jobWords = [
                    'developer','engineer','designer','manager','director','founder','ceo',
                    'student','intern','analyst','consultant','specialist','lead','head',
                    'officer','scientist','architect','professor','teacher','coordinator',
                    'recruiter','freelance','software','senior','junior','mid','full stack',
                    'frontend','backend','devops','mobile','data','product','project','marketing',
                    'sales','finance','accounting','operations','research','associate','executive',
                    'at ','@ ','|'
                ];

                for (const line of lines) {
                    const low = line.toLowerCase();
                    const isLoc = locationWords.some(w => low.includes(w));
                    const isJob = jobWords.some(w => low.includes(w));

                    if (!headline && (isJob || (!isLoc && line.length > 5))) {
                        headline = line;
                    } else if (!location && isLoc && !isJob) {
                        location = line;
                    } else if (headline && !location && line !== headline) {
                        location = line;
                        break;
                    }
                }

                // If headline looks like a location (no job words), swap
                if (headline && !location) {
                    const hLow = headline.toLowerCase();
                    if (locationWords.some(w => hLow.includes(w)) && !jobWords.some(w => hLow.includes(w))) {
                        location = headline;
                        headline = '';
                    }
                }
            }

            if (!name || name.length < 2) return;

            seen.add(href);
            results.push({
                name: name,
                headline: headline || '',
                location: location || '',
                profile_url: href
            });
        });
    }

    // --- Method 2: Fallback using direct profile link parsing ---
    if (results.length === 0) {
        const links = document.querySelectorAll('a[href*="/in/"]');
        links.forEach(link => {
            const href = link.href.split('?')[0];
            if (seen.has(href) || href.includes('/in/me/') || href.includes('/in/ACoA')) return;

            let card = link.closest('li') || link.parentElement?.parentElement?.parentElement;
            if (!card) return;

            const ariaSpan = link.querySelector('span[aria-hidden="true"]');
            const name = ariaSpan ? clean(ariaSpan.textContent) :
                clean(link.textContent.split('\\n').filter(l => clean(l).length > 1)[0] || '');
            if (!name || name.length < 2) return;

            const lines = card.innerText.split('\\n')
                .map(l => clean(l))
                .filter(l => l.length > 1 && l !== name && !isJunk(l));

            seen.add(href);
            results.push({
                name: name,
                headline: lines[0] || '',
                location: lines[1] || '',
                profile_url: href
            });
        });
    }

    return results;
}"""


def save_as_toml(connections, filepath):
    lines = [
        "# LinkedIn Connections",
        f"# Scraped: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# Total: {len(connections)}",
        "",
    ]
    for conn in connections:
        n = conn.get("name", "").replace('"', "'")
        h = conn.get("headline", "").replace('"', "'").replace("\n", " ")
        loc = conn.get("location", "").replace('"', "'").replace("\n", " ")
        u = conn.get("profile_url", "")
        lines += [
            "[[connections]]",
            f'name = "{n}"',
            f'headline = "{h}"',
            f'location = "{loc}"',
            f'url = "{u}"',
            "",
        ]
    filepath.write_text("\n".join(lines), encoding="utf-8")


def load_from_toml(filepath):
    if not filepath.exists():
        return []
    text = filepath.read_text(encoding="utf-8")
    connections, current = [], {}
    for line in text.splitlines():
        line = line.strip()
        if line == "[[connections]]":
            if current:
                connections.append(current)
            current = {}
        elif line.startswith("name = "):
            current["name"] = line.split("= ", 1)[1].strip('"')
        elif line.startswith("headline = "):
            current["headline"] = line.split("= ", 1)[1].strip('"')
        elif line.startswith("location = "):
            current["location"] = line.split("= ", 1)[1].strip('"')
        elif line.startswith("url = "):
            current["profile_url"] = line.split("= ", 1)[1].strip('"')
    if current:
        connections.append(current)
    return connections


async def scrape_connections(session_file=None):
    """Scrape LinkedIn connections. Optionally specify which session file to use."""
    if session_file is None:
        session_file = AUTH_DIR / "linkedin.json"

    if not session_file.exists():
        print(f"[Connections] No saved session at {session_file.name}. Run: python run.py login")
        return []

    connections = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=str(session_file))
        page = await context.new_page()

        try:
            print("[Connections] Opening connections page...")
            await page.goto(
                "https://www.linkedin.com/mynetwork/invite-connect/connections/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(5000)

            if "/login" in page.url:
                print("[Connections] Session expired! Run: python run.py login")
                await browser.close()
                return []

            # Paginated search — 1st-degree connections
            print("[Connections] Scraping via search pagination...")
            all_raw = []
            page_num = 1
            empty_pages = 0
            seen_urls = set()
            MAX_PAGES = 100

            while empty_pages < 3:
                if page_num > MAX_PAGES:
                    print(f"\n[Connections] Reached page cap ({MAX_PAGES}); stopping.")
                    break
                url = f"https://www.linkedin.com/search/results/people/?network=%5B%22F%22%5D&page={page_num}"
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

                # Scroll down to load all results on this page
                for _ in range(4):
                    await page.keyboard.press("End")
                    await page.wait_for_timeout(800)

                batch = await page.evaluate(EXTRACT_JS)

                # Count only genuinely new profiles: LinkedIn clamps out-of-range
                # pages to the last valid page, re-serving the same non-empty batch.
                new_items = []
                for item in batch or []:
                    key = item.get("profile_url", "") or item.get("name", "").strip()
                    if key and key not in seen_urls:
                        seen_urls.add(key)
                        new_items.append(item)

                if new_items:
                    all_raw.extend(new_items)
                    empty_pages = 0
                    sys.stdout.write(
                        f"\r  Page {page_num}: +{len(new_items)} (total: {len(all_raw)})"
                    )
                    sys.stdout.flush()
                else:
                    empty_pages += 1

                page_num += 1
                await page.wait_for_timeout(1500)

            print(f"\n[Connections] Scraped {len(all_raw)} entries across {page_num - 1} pages")

            # Deduplicate
            print("[Connections] Deduplicating...")
            seen = set()
            for item in all_raw:
                url = item.get("profile_url", "")
                name = item.get("name", "").strip()
                if not name or len(name) < 2:
                    continue
                key = url or name
                if key in seen:
                    continue
                seen.add(key)
                connections.append(
                    {
                        "name": name,
                        "headline": item.get("headline", "").strip(),
                        "location": item.get("location", "").strip(),
                        "profile_url": url,
                    }
                )

            print(f"[Connections] Extracted: {len(connections)} connections")
            await context.storage_state(path=str(session_file))

        except Exception as e:
            print(f"\n[Connections] Error: {e}")
            try:
                await page.screenshot(
                    path=str(
                        BASE_DIR
                        / "logs"
                        / f"conn_err_{datetime.now():%Y%m%d_%H%M%S}.png"
                    )
                )
            except Exception:
                pass
        finally:
            await browser.close()

    if connections:
        save_as_toml(connections, CONNECTIONS_FILE)
        tmp = CONNECTIONS_JSON.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(connections, f, indent=2, ensure_ascii=False)
        tmp.replace(CONNECTIONS_JSON)
        print(f"[Connections] Saved: {CONNECTIONS_FILE.name}")

    return connections


def _kw_pattern(word: str) -> str:
    """Regex for one category keyword, anchored on word boundaries only where
    the keyword actually starts/ends with a word character. Anchoring blindly
    would break the punctuation-led entries: "\\b" before the "." of ".net"
    never matches, because a space followed by "." is not a word boundary."""
    esc = re.escape(word)
    left = r"\b" if word[:1].isalnum() else ""
    right = r"\b" if word[-1:].isalnum() else ""
    return f"{left}{esc}{right}"


def analyze_audience(connections=None):
    if connections is None:
        # Prefer JSON (more reliable on Windows; TOML can fail on Arabic chars)
        if CONNECTIONS_JSON.exists():
            with open(CONNECTIONS_JSON, "r", encoding="utf-8") as f:
                connections = json.load(f)
        elif CONNECTIONS_FILE.exists():
            connections = load_from_toml(CONNECTIONS_FILE)
        else:
            print("[Audience] No data. Run: python run.py connections")
            return {}
    if not connections:
        return {}

    cats = {
        "developers": [],
        "designers": [],
        "recruiters": [],
        "founders_ceos": [],
        "managers": [],
        "students": [],
        "data_ai": [],
        "other": [],
    }
    kw = {
        "developers": [
            "developer", "engineer", "programmer", "frontend", "backend",
            "fullstack", "full-stack", "full stack", "software", "react",
            "angular", "node", "python", "java", "devops", ".net", "flutter",
            "mobile", "ios", "android", "php", "laravel", "django", "vue",
            "next.js", "typescript",
        ],
        "designers": [
            "designer", "ux", "ui", "product design", "graphic", "creative",
            "brand", "visual",
        ],
        "recruiters": [
            "recruiter", "talent", "hiring", "hr", "human resources",
            "acquisition",
        ],
        "founders_ceos": [
            "founder", "ceo", "co-founder", "entrepreneur", "owner", "cto",
            "coo",
        ],
        "managers": [
            "manager", "lead", "director", "head of", "vp", "principal",
            "supervisor", "team lead",
        ],
        "students": [
            "student", "intern", "trainee", "fresh grad", "junior",
            "learning", "aspiring",
        ],
        "data_ai": [
            "data", " ai ", "machine learning", "ml ", "artificial intelligence",
            "analyst", "scientist", "nlp", "deep learning",
        ],
    }

    # data_ai needs word-boundary matching so headlines that start or end with
    # "AI"/"ML" (e.g. "AI Engineer", "Lead, ML") are not missed by the
    # surrounding-space keywords " ai " / "ml ".
    data_ai_re = re.compile(
        r"\b(?:ai|ml|data|machine\s*learning|artificial\s*intelligence|"
        r"analyst|scientist|nlp|deep\s*learning)\b"
    )
    # Every other bucket needs the same word-boundary treatment data_ai already
    # got. Plain `w in h` matched keywords buried inside ordinary words, and
    # since first-match wins the mislabel is permanent: "ui" hides in
    # "building", so "Full Stack Developer building web apps" was filed under
    # designers, and "hr" hides in "Bahrain", so "Backend Engineer at Bahrain
    # Tech" was filed under recruiters. Those wrong counts land in
    # audience_insights.json and then steer article scoring.
    cat_res = {c: re.compile("|".join(_kw_pattern(w) for w in words))
               for c, words in kw.items()}
    cat_res["data_ai"] = data_ai_re

    # First-match wins, so check the most specific buckets BEFORE the broad
    # "developers" bucket. Otherwise a "Data Engineer" / "ML Engineer" headline
    # matches the generic "engineer" keyword and is claimed by developers before
    # the dedicated data_ai regex ever runs (and "lead"/"cto" let managers /
    # founders_ceos swallow data-AI practitioners too). Ordering data_ai first
    # and developers last restores the intended classification. (audit-7 2.C)
    priority = ["data_ai", "recruiters", "designers", "founders_ceos",
                "managers", "students", "developers"]
    for c in connections:
        h = c.get("headline", "").lower()
        found = False
        for cat in priority:
            if cat_res[cat].search(h):
                cats[cat].append(c)
                found = True
                break
        if not found:
            cats["other"].append(c)

    total = len(connections)
    insights = {
        "total_connections": total,
        "scraped_at": datetime.now().isoformat(),
        "breakdown": {},
        "top_titles": [],
        "recommendations": [],
    }

    print(f"\n{'=' * 50}\n  AUDIENCE INSIGHTS ({total} connections)\n{'=' * 50}\n")
    for cat, members in sorted(cats.items(), key=lambda x: len(x[1]), reverse=True):
        cnt = len(members)
        pct = round((cnt / total) * 100, 1) if total else 0
        insights["breakdown"][cat] = {
            "count": cnt,
            "percentage": pct,
            "sample_headlines": [m.get("headline", "") for m in members[:5]],
        }
        if cnt:
            print(
                f"  {cat.replace('_', ' ').title():.<20} {cnt:>4} ({pct:>5.1f}%)  {'#' * int(pct / 2)}"
            )

    # Extract keywords from connections' job titles + companies (from headline)
    # NOT from names, degree labels, or generic relationship words
    words = {}
    stop = {"the", "a", "an", "at", "in", "on", "and", "or", "of", "to", "for",
            "is", "with", "this", "that", "from", "are", "was", "been", "have",
            "has", "had", "will", "would", "could", "should", "about", "more",
            "just", "like", "your", "their", "them", "they", "what", "when",
            "how", "who", "which", "where", "than", "also", "into", "over",
            "such", "after", "before", "between", "each", "most", "some",
            "other", "very", "only", "even", "here", "there", "then", "these",
            "those", "being", "doing", "make", "made", "people", "work", "need",
            # English LinkedIn UI noise
            "mutual", "connection", "connections", "follower", "followers",
            "follow", "following", "view", "viewed", "profile", "profiles",
            "linkedin", "premium", "see", "all", "show", "more", "less",
            "open", "available", "hiring", "promoted",
            # Common English stopwords missed
            "you", "your", "yours", "yourself", "myself", "himself", "herself",
            "ourself", "yourselves", "ourselves", "themselves",
            # Generic headline filler (these are technically job words but too vague)
            "experience", "years", "year", "skilled", "working",
            "passionate", "aspiring", "currently", "seeking", "looking",
            "professional", "self", "ago", "over", "around", "exp"}

    # Common first names (English + Arabic) — these dominate names lists
    # and add zero signal about what connections DO professionally
    first_names = {
        # English first names common in MENA tech
        "mohamed", "mohammed", "mohammad", "muhamed", "ahmed", "ahmad",
        "ali", "omar", "yousef", "youssef", "yusuf", "ibrahim", "abdullah",
        "abdelrahman", "abdul", "khaled", "khaleed", "hassan", "hussein",
        "mahmoud", "mahmud", "mostafa", "mustafa", "mohsen", "saeed", "said",
        "tarek", "tareq", "ehab", "amr", "amer", "wael", "walid", "ziad",
        "ramy", "rami", "kareem", "karim", "samer", "sameh", "saif", "yahya",
        "anas", "adam", "adel", "ashraf", "atef", "ayman", "bassem", "bassam",
        "essam", "fadi", "fady", "farid", "ghassan", "haitham", "hamza", "hany",
        "hosam", "hossam", "imad", "kamal", "lutfi", "majed", "marwan", "muneer",
        "nabil", "nader", "nadir", "naif", "nasser", "nizar", "rafik", "raed",
        "ragab", "rashid", "salah", "salem", "sami", "samir", "shadi", "sherif",
        "sultan", "talal", "tamer", "tarek", "tariq", "thaer", "wajdi", "wassim",
        "yasser", "yasser", "zaher", "zaki", "ziyad",
        # Female names
        "fatma", "fatima", "aya", "amina", "amal", "aisha", "asma", "asmaa",
        "doaa", "doha", "dina", "esraa", "eman", "esra", "esma", "farida",
        "ghada", "hagar", "hala", "hanan", "hanaa", "heba", "huda", "hosna",
        "iman", "jana", "jihad", "khadija", "lamia", "lubna", "maha", "mai",
        "mariam", "maryam", "menna", "mona", "noha", "noor", "nour", "rahma",
        "rana", "reem", "rim", "rosa", "salma", "sara", "sarah", "shahd",
        "sheren", "soha", "yasmin", "yasmine", "zainab", "zeinab", "zeina",
        # Common Western names
        "alex", "andrew", "ben", "chris", "dan", "daniel", "david", "james",
        "john", "kevin", "mark", "mike", "michael", "paul", "peter", "robert",
        "sam", "steve", "tom", "tony", "will", "william",
        # Common single-letter / abbreviations
        "abd", "abu", "bin", "ibn", "el", "al", "ela",
    }

    # Arabic LinkedIn UI noise tokens (degree, mutual, follower, etc.)
    # These appear in connection cards and dominate the keyword extraction
    arabic_ui_noise = {
        "الدرجة", "الأولى", "الثانية", "الثالثة", "درجة",   # degree labels
        "زميل", "زميلان", "زملاء", "مشترك", "مشتركون", "مشتركان",  # mutual conn
        "متابع", "متابعون", "متابعة", "يتابع",      # follower/following
        "صفحة", "صفحات", "ملف", "شخصي",            # profile labels
        "شركة", "في", "من", "إلى", "على", "عن",   # generic prepositions
        "أن", "إن", "كان", "كانت", "هذا", "هذه", "هؤلاء", "ذلك", "تلك",
        "كل", "بعض", "أكثر", "أقل", "ما", "لا", "نعم", "أو", "و",
        "الذي", "التي", "الذين", "اللاتي", "اللواتي",
    }

    # Use headlines (job titles + companies) — that's what the user actually
    # shared. Skip names with the first_names blacklist.
    post_texts = []
    for c in connections:
        posts = c.get("recent_posts", [])
        if posts:
            post_texts.extend(posts)

    source_label = "posts"
    if not post_texts:
        post_texts = [c.get("headline", "") for c in connections]
        source_label = "headlines (job titles + companies)"

    # First, strip out the connection NAMES from each headline so they don't
    # leak as keywords (each connection's own name is often repeated in
    # mentions / signoffs within the headline area).
    name_tokens = set()
    for c in connections:
        for token in re.findall(r"[A-Za-z؀-ۿ]{3,}", c.get("name", "")):
            name_tokens.add(token.lower())

    # Combine and tokenize — \w includes Arabic letters in Python re
    combined_text = " ".join(post_texts)
    for w in re.findall(r"\w+", combined_text):
        wl = w.lower()
        # Filter: skip stop words, Arabic UI noise, short tokens, digits,
        # first names, AND any token that matched a connection's own name
        if wl in stop:
            continue
        if wl in first_names:
            continue
        if wl in name_tokens:
            continue
        if w in arabic_ui_noise or wl in arabic_ui_noise:
            continue
        if len(w) <= 3:
            continue
        if w.isdigit():
            continue
        # Skip if it's >50% digits (e.g. version numbers, page IDs)
        digit_ratio = sum(1 for ch in w if ch.isdigit()) / len(w)
        if digit_ratio > 0.5:
            continue
        words[wl] = words.get(wl, 0) + 1

    top = sorted(words.items(), key=lambda x: x[1], reverse=True)[:20]
    insights["top_titles"] = [{"word": w, "count": c} for w, c in top]

    print(f"\n  Top keywords (from {source_label}):")
    [print(f"    {w}: {c}x") for w, c in top[:10]]

    dp = insights["breakdown"].get("developers", {}).get("percentage", 0)
    if dp > 40:
        insights["recommendations"].append(
            "Heavy dev audience - technical posts perform well"
        )
    insights["recommendations"].append(
        "Mix technical depth with accessible explanations"
    )

    # Collect unique locations
    locations = {}
    for c in connections:
        loc = c.get("location", "").strip()
        if loc and len(loc) > 2:
            locations[loc] = locations.get(loc, 0) + 1
    top_locs = sorted(locations.items(), key=lambda x: x[1], reverse=True)[:10]
    insights["top_locations"] = [{"location": loc, "count": cnt} for loc, cnt in top_locs]

    if top_locs:
        print(f"\n  Top locations:")
        [print(f"    {loc}: {cnt}x") for loc, cnt in top_locs[:5]]

    tmp = CONNECTIONS_SUMMARY_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(insights, f, indent=2, ensure_ascii=False)
    tmp.replace(CONNECTIONS_SUMMARY_FILE)
    print(f"\n[Audience] Saved: {CONNECTIONS_SUMMARY_FILE.name}")
    return insights


if __name__ == "__main__":
    if "--analyze" in sys.argv:
        analyze_audience()
    else:
        conns = asyncio.run(scrape_connections())
        if conns:
            analyze_audience(conns)
