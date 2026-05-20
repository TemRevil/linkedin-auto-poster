# LinkedIn Auto-Poster

Automated LinkedIn posting system that scrapes news for **your chosen topic**
(AI, finance, fitness, design — anything), writes humanized posts tailored to
your profile and audience, and posts them after your approval.

## ⚡ Install with Claude Code (easiest)

If you have [Claude Code](https://claude.com/claude-code), just paste this:

```
Read https://raw.githubusercontent.com/TemRevil/linkedin-auto-poster/main/docs/SETUP_WITH_CLAUDE.md and set it up for me.
```

Claude reads [`docs/SETUP_WITH_CLAUDE.md`](docs/SETUP_WITH_CLAUDE.md), then clones the repo,
installs dependencies, launches the dashboard, and walks you through the
first-run setup wizard. (Replace the URL with your fork if you renamed the repo.)

Prefer to do it by hand? See [Manual setup](#setup) below.

## How It Works

```
5:00 PM daily
    |
    v
[1] Scrape news for YOUR topic (RSS feeds + web + Gmail newsletters)
    |
    v
[2] Pick best item based on your profile + audience data
    |
    v
[3] Generate post -> run through humanizer (removes AI writing patterns)
    |
    v
[4] Open approval page (http://127.0.0.1:5555) + send notification
    |
    v
[5] You review / edit / approve
    |
    v
[6] Post to LinkedIn via Playwright
```

## Setup

This project ships with **no personal data**. On first launch the dashboard
shows a setup wizard that creates your `profile.md` and settings for you — or
copy the templates manually (`profile.example.md` → `profile.md`,
`my_past_posts.example.md` → `my_past_posts.md`).

```bash
# 1. Install dependencies
python src/run.py setup

# 2. Start the dashboard and complete the first-run setup wizard
python src/run.py approve        # opens http://127.0.0.1:5555

# 3. Login to LinkedIn (one-time, saves session) — also doable from the UI
python src/run.py login

# 4. Scrape your connections for audience targeting
python src/run.py connections

# 5. Schedule the daily task (run PowerShell as admin)
.\schedule_task.ps1              # or .\schedule_task.bat

# 6. (Optional) Connect Gmail for newsletter scraping
python src/gmail_setup.py
```

> **Privacy:** your profile, past posts, connections, sessions, API keys, and
> generated posts are all gitignored. Only `*.example.*` templates are tracked.

## Commands

> All commands run from the project root. `src/run.py` is the entry point.

| Command | What it does |
|---|---|
| `python src/run.py setup` | Install all dependencies (pip + playwright) |
| `python src/run.py login` | Open browser to login to LinkedIn (one-time) |
| `python src/run.py connections` | Scrape all your LinkedIn connections to TOML |
| `python src/run.py analyze` | Re-analyze existing connections data |
| `python src/run.py scrape` | Scrape news only (no post generation) |
| `python src/run.py generate` | Scrape news + generate a post draft |
| `python src/run.py approve` | Open the approval web page |
| `python src/run.py post` | Post all approved drafts to LinkedIn |
| `python src/run.py full` | Full pipeline (scrape + generate + approve + post) |
| `python src/run.py test` | Test run (scrape + generate + open approval page) |
| `python src/run.py status` | Show system status (session, drafts, etc.) |

## File Structure

```
linkedin-auto-poster/
  README.md  LICENSE  requirements.txt  .gitignore
  _post_context_template.md   # Prompt template for generation
  setup.bat                   # One-click dependency installer
  schedule_task.ps1 / .bat    # Windows Task Scheduler (daily 5 PM)
  setup_scheduled_tasks.bat   # Registers discovery + generation tasks

  src/                        # All Python modules
    run.py                    #   Master entry point (all commands)
    config.py                 #   Settings, paths, feeds, topic getters
    approval_server.py        #   Flask dashboard + JSON API
    post_generator.py         #   Picks topic, writes post, humanizes
    humanizer.py              #   Removes AI writing patterns
    news_scraper.py           #   RSS + web + Gmail scraping
    news_discovery.py         #   Swipe-card discovery + scoring
    linkedin_poster.py        #   Playwright automation to post
    connections_scraper.py    #   Scrapes LinkedIn connections
    scheduler.py              #   Orchestrates the daily pipeline
    database.py  builder_posts.py  gmail_setup.py  clean_connections.py

  templates/index.html        # The dashboard UI (single file)
  examples/                   # *.example.* templates (copied on setup)
  docs/SETUP_WITH_CLAUDE.md   # Claude Code install runbook

  # Created at runtime, all gitignored:
  profile.md  my_past_posts.md  settings.json  connections.toml
  auth_state/  posts/  images/  logs/  discovery.db
```

## Post Strategy

Based on profile.md:

- **40% AI news analysis** - break down what happened, why it matters
- **40% Builder updates** - sharing what you're building with AI
- **20% Quick takes** - short opinions on AI tools and trends

Posts are humanized using 29 anti-AI-writing rules (based on blader/humanizer):
no significance inflation, no filler transitions, no em-dash overuse,
no sycophantic openers, no promotional language, max 3-5 hashtags.

## Approval Page

Dark UI at `http://127.0.0.1:5555`:
- Read the generated post
- Edit it directly in-browser
- Approve (queues for posting) or Reject
- See recent post history
- Windows notification every 30 min if unapproved

## Image Sources

The system auto-detects when a post needs an image and provides links to:
- **Unsplash** - best quality, free
- **Pexels** - free, good variety
- **Pixabay** - free, large library

## News Sources & Topic

The default feeds cover AI/tech, but **you choose the niche**. In
**Settings → General → Content focus** set any topic (e.g. "personal finance",
"climate", "indie games") and optionally paste your own **Custom RSS feeds**
(one URL per line). Discovery, web search, and Gmail scoping all follow it.

- RSS: your custom feeds, or the built-in AI/tech defaults
- Web: search queries derived from your topic (via Playwright)
- Gmail: newsletter emails scoped to your topic (optional)

## Audience Data

Your connections are scraped to `connections.toml` and analyzed into
`audience_insights.json`: a breakdown by role (developers, designers,
recruiters, founders, students) plus the top keywords across your network.
The post generator uses this so topics skew toward what your audience cares
about. Nothing here is shared — both files are gitignored.
