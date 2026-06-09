"""
Master runner for LinkedIn Auto-Poster.
Single entry point for all operations.

Usage:
    python run.py setup          Full first-time setup
    python run.py login          Login to LinkedIn (saves session)
    python run.py connections    Scrape your LinkedIn connections
    python run.py scrape         Just scrape AI news
    python run.py generate       Scrape + generate post draft
    python run.py approve        Open approval page
    python run.py post           Post approved drafts
    python run.py full           Full daily pipeline
    python run.py test           Test run (scrape + generate + show approval page)
"""
import asyncio
import sys
import subprocess
import webbrowser
from pathlib import Path

# Ensure we're in the right directory
sys.path.insert(0, str(Path(__file__).parent))


def cmd_setup():
    """Install all dependencies."""
    print("\n[Setup] Installing Python dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    print("\n[Setup] Installing Playwright Chromium...")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    print("\n[Setup] Done! Next steps:")
    print("  1. python run.py login")
    print("  2. python run.py connections")
    print("  3. python run.py test")


def cmd_login():
    """Login to LinkedIn."""
    from linkedin_poster import login_and_save_session
    asyncio.run(login_and_save_session())


def cmd_connections():
    """Scrape connections."""
    from connections_scraper import scrape_connections, analyze_audience
    connections = asyncio.run(scrape_connections())
    if connections:
        analyze_audience(connections)


def cmd_connections_analyze():
    """Analyze existing connections data."""
    from connections_scraper import analyze_audience
    analyze_audience()


def cmd_scrape():
    """Just scrape news."""
    from news_scraper import gather_all_news
    articles = asyncio.run(gather_all_news())
    print(f"\nFound {len(articles)} articles. Top 5:")
    for i, a in enumerate(articles[:5], 1):
        print(f"  {i}. [{a['type']}] {a['title'][:70]}")


def cmd_generate():
    """Scrape + generate a post draft."""
    from news_scraper import gather_all_news
    from post_generator import generate_post_draft, load_profile, pick_best_topic

    print("\n[Pipeline] Scraping AI news...")
    articles = asyncio.run(gather_all_news())

    if not articles:
        print("No articles found. Try again later.")
        return

    print("\n[Pipeline] Generating post...")
    profile = load_profile()
    best = pick_best_topic(articles, profile)
    draft = generate_post_draft(best, profile)

    print(f"\n{'=' * 50}")
    print("  GENERATED POST DRAFT")
    print(f"{'=' * 50}")
    print(f"\nTopic: {best['title'][:70]}")
    print(f"Source: {best['source']}")
    print(f"Needs image: {draft['needs_image']}")
    print(f"\n--- POST CONTENT ---\n")
    print(draft['post_content'])
    print(f"\n--- END ---\n")
    print(f"Draft saved. Open approval page: python run.py approve")


def cmd_approve():
    """Start approval server."""
    from approval_server import run_server
    run_server(open_browser=True)


def cmd_post():
    """Post approved drafts."""
    from linkedin_poster import post_approved_drafts
    asyncio.run(post_approved_drafts())


def cmd_full():
    """Full daily pipeline."""
    from scheduler import daily_pipeline, run_with_server
    run_with_server()


def cmd_test():
    """Test run: scrape + generate + open approval page."""
    from news_scraper import gather_all_news
    from post_generator import generate_post_draft, load_profile, pick_best_topic
    from approval_server import run_server, send_reminder_notification

    print("\n" + "=" * 60)
    print("  TEST RUN - LinkedIn Auto-Poster")
    print("=" * 60)

    # Scrape
    print("\n[1/3] Scraping AI news...")
    articles = asyncio.run(gather_all_news())

    if not articles:
        print("  No articles found. Check your internet connection.")
        return

    # Generate
    print("\n[2/3] Generating post draft...")
    profile = load_profile()
    best = pick_best_topic(articles, profile)
    draft = generate_post_draft(best, profile)

    print(f"\n  Topic: {best['title'][:60]}")
    print(f"  Source: {best['source']}")
    print(f"\n  --- PREVIEW ---")
    print(f"  {draft['post_content'][:200]}...")
    print(f"  --- END PREVIEW ---\n")

    # Notify
    print("[3/3] Opening approval page...")
    send_reminder_notification()

    # Start server
    print(f"\n  Approval page: http://127.0.0.1:5555")
    print(f"  Review the post, edit if needed, then approve.")
    print(f"  After approval, run: python run.py post\n")
    run_server(open_browser=True)


def cmd_status():
    """Show current system status."""
    from config import AUTH_DIR, POSTS_DIR

    print(f"\n{'=' * 50}")
    print("  SYSTEM STATUS")
    print(f"{'=' * 50}\n")

    # LinkedIn session
    auth_file = AUTH_DIR / "linkedin.json"
    print(f"  LinkedIn session: {'Active' if auth_file.exists() else 'Not logged in'}")

    # Connections
    conn_file = Path(__file__).resolve().parent.parent / "connections.json"
    if conn_file.exists():
        import json
        with open(conn_file, "r", encoding="utf-8") as f:
            conns = json.load(f)
        print(f"  Connections scraped: {len(conns)}")
    else:
        print(f"  Connections scraped: Not yet")

    # Gmail
    gmail_file = AUTH_DIR / "gmail_token.json"
    print(f"  Gmail connected: {'Yes' if gmail_file.exists() else 'No (optional)'}")

    # Drafts
    pending = list(POSTS_DIR.glob("draft_*.json"))
    print(f"  Total drafts: {len(pending)}")

    import json
    pending_count = 0
    approved_count = 0
    posted_count = 0
    for f in pending:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
        except (json.JSONDecodeError, OSError):
            continue
        status = d.get("status")
        if status == "pending_approval":
            pending_count += 1
        elif status == "approved":
            approved_count += 1
        elif status == "posted":
            posted_count += 1

    print(f"    Pending approval: {pending_count}")
    print(f"    Approved (ready): {approved_count}")
    print(f"    Posted: {posted_count}")

    # Profile
    profile_file = Path(__file__).resolve().parent.parent / "profile.md"
    print(f"  Profile configured: {'Yes' if profile_file.exists() else 'No'}")
    print()


def cmd_discover():
    """Run 3 PM news discovery — scrape and prepare swipe cards."""
    from news_discovery import discover_news
    cards = asyncio.run(discover_news())
    if cards:
        print(f"\n{len(cards)} discovery cards ready.")
        print(f"Open the dashboard to swipe: python run.py approve")
    else:
        print("\nNo news found for discovery.")


COMMANDS = {
    "setup": cmd_setup,
    "login": cmd_login,
    "connections": cmd_connections,
    "analyze": cmd_connections_analyze,
    "scrape": cmd_scrape,
    "generate": cmd_generate,
    "discover": cmd_discover,
    "approve": cmd_approve,
    "post": cmd_post,
    "full": cmd_full,
    "test": cmd_test,
    "status": cmd_status,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        print("Available commands:")
        for cmd in COMMANDS:
            print(f"  python run.py {cmd}")
        print()
        sys.exit(0)

    command = sys.argv[1]
    if command in COMMANDS:
        COMMANDS[command]()
    else:
        print(f"Unknown command: {command}")
        print(f"Run 'python run.py --help' for available commands.")
        sys.exit(1)
