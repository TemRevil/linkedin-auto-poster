# LinkedIn Auto-Poster

Automated LinkedIn posting system that scrapes news for **your chosen topic**
(AI, finance, fitness, design — anything), writes humanized posts tailored to
your profile and audience, and posts them after your approval.

## ⚡ Install with Claude Code (easiest)

If you have [Claude Code](https://claude.com/claude-code), just paste this:

```
Read https://raw.githubusercontent.com/TemRevil/linkedin-auto-poster/main/SETUP_WITH_CLAUDE.md and set it up for me.
```

Claude reads [`SETUP_WITH_CLAUDE.md`](SETUP_WITH_CLAUDE.md), then clones the repo,
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
python run.py setup

# 2. Start the dashboard and complete the first-run setup wizard
python run.py approve        # opens http://127.0.0.1:5555

# 3. Login to LinkedIn (one-time, saves session) — also doable from the UI
python run.py login

# 4. Scrape your connections for audience targeting
python run.py connections

# 5. Schedule the daily task (run PowerShell as admin)
.\schedule_task.ps1          # or .\schedule_task.bat

# 6. (Optional) Connect Gmail for newsletter scraping
python gmail_setup.py
```

> **Privacy:** your profile, past posts, connections, sessions, API keys, and
> generated posts are all gitignored. Only `*.example.*` templates are tracked.

## Commands

| Command | What it does |
|---|---|
| `python run.py setup` | Install all dependencies (pip + playwright) |
| `python run.py login` | Open browser to login to LinkedIn (one-time) |
| `python run.py connections` | Scrape all your LinkedIn connections to TOML |
| `python run.py analyze` | Re-analyze existing connections data |
| `python run.py scrape` | Scrape AI news only (no post generation) |
| `python run.py generate` | Scrape news + generate a post draft |
| `python run.py approve` | Open the approval web page |
| `python run.py post` | Post all approved drafts to LinkedIn |
| `python run.py full` | Full pipeline (scrape + generate + approve + post) |
| `python run.py test` | Test run (scrape + generate + open approval page) |
| `python run.py status` | Show system status (session, drafts, etc.) |

## File Structure

```
Linkedin_A01/
  run.py                  # Master entry point (all commands)
  config.py               # Settings (time, ports, feeds, image sources)
  profile.example.md      # Template — copied to profile.md by the setup wizard
  profile.md              # Your profile, audience, voice (gitignored)
  connections.toml        # Your LinkedIn connections (gitignored)
  audience_insights.json  # Audience breakdown from connections
  news_scraper.py         # Scrapes AI news (RSS + web + Gmail)
  post_generator.py       # Picks topic, writes post, applies humanizer
  humanizer.py            # Removes 29 AI writing patterns
  approval_server.py      # Local HTML approval page + notifications
  linkedin_poster.py      # Playwright automation to post on LinkedIn
  scheduler.py            # Orchestrates the daily pipeline
  connections_scraper.py  # Scrapes LinkedIn connections via search
  gmail_setup.py          # Optional Gmail API setup
  schedule_task.ps1       # Windows Task Scheduler (PowerShell)
  schedule_task.bat       # Windows Task Scheduler (CMD)
  setup.bat               # One-click dependency installer
  auth_state/             # Saved LinkedIn + Gmail sessions
  posts/                  # Draft files (JSON) + raw news data
  images/                 # Downloaded post images
  logs/                   # Error screenshots + logs
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
