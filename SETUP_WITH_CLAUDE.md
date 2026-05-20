# Set up LinkedIn Auto-Poster (instructions for Claude Code)

> **How to use this file:** copy its link and paste it to Claude Code like:
> *"Read https://raw.githubusercontent.com/TemRevil/linkedin-auto-poster/main/SETUP_WITH_CLAUDE.md and set this up for me."*
> Claude will follow the steps below to clone, install, and launch the app.

---

## You are Claude Code. Do the following for the user.

This sets up a self-hosted dashboard that scrapes news for the user's chosen
topic, writes LinkedIn posts in their voice, and publishes them after approval.

### 0. Confirm prerequisites
Run these and tell the user if anything is missing:
```bash
python --version    # need Python 3.11+ (3.13 recommended)
git --version
```
- If Python is missing: Windows `winget install Python.Python.3.13`; macOS `brew install python3`; Linux `sudo apt install python3 python3-venv`.
- Claude Code itself is required to generate posts with the default engine. If the user doesn't have it, they can instead set an Anthropic API key later in the app (Settings → AI Engine).

### 1. Clone the repository
Pick a working folder the user wants, then:
```bash
git clone https://github.com/TemRevil/linkedin-auto-poster.git
cd linkedin-auto-poster
```
> If the repo was renamed/forked, use that URL instead.

### 2. Create a virtual environment and install dependencies
```bash
# Windows (PowerShell)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium

# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```
If `python run.py setup` exists it does the pip + playwright step in one go — you may run that instead.

### 3. Start the dashboard
```bash
python approval_server.py
# or:  python run.py approve
```
Then open **http://127.0.0.1:5555** in a browser. A **first-run setup wizard**
appears — walk the user through it (their name, role, **what they post about**,
GitHub username, voice). It writes `profile.md` and saves settings. Nothing is
shared; all personal files are gitignored.

### 4. Connect LinkedIn (one-time)
In the dashboard: **Settings → Accounts → Login**. A browser opens; the user
signs in once and the session is saved to `auth_state/` (gitignored).
CLI alternative: `python run.py login`.

### 5. (Optional) Pick the content niche precisely
- **Settings → General → Content focus**: free-text topic (e.g. "personal
  finance", "indie game dev"). Not limited to AI/tech.
- **Custom RSS feeds**: paste feed URLs for that niche (one per line). Empty =
  built-in AI/tech feeds.
- **Settings → Voice**: paste 3–5 of the user's real posts to calibrate tone.

### 6. (Optional) Gmail-grounded posts
If the user wants posts based on their newsletters: **Settings → General →
Gmail method**. `Claude Code (MCP)` needs Gmail connected in their Claude
account; `OAuth credentials.json` is uploaded in the Accounts tab.

### 7. (Optional) Schedule daily runs (Windows)
```bash
.\setup_scheduled_tasks.bat   # registers discovery + generation tasks
```

## How the user uses it day-to-day
1. **Home** → type what they want (e.g. "hot take on <topic>"); watch live progress.
2. **Discover** → swipe news cards to train topic taste.
3. **Posts → Pending** → edit, add images (multiple = carousel), then **Approve** to publish.
4. **Posts → Failed** → shows the exact error + a Retry button if a post didn't go through.
5. **Docs** (sidebar) → full in-app guide; **Setup & help** re-runs the wizard.

## Notes for you (Claude)
- Do **not** commit or print any secrets. `profile.md`, `settings.json`,
  `connections.*`, `auth_state/`, API keys, and `posts/` are all gitignored.
- The app reads `config.py` (relative paths) and `settings.json` (created by the
  wizard). Defaults work out of the box for an AI/tech niche.
- If `playwright` errors on launch, re-run `python -m playwright install chromium`.
- Default port is `5555` (`config.py → APPROVAL_PORT`).
