# LinkedIn Auto-Poster — Daily Bug Fix Maintenance
# Runs Claude Code CLI at 4:00 PM to fix the next audit issue, commit, and push.
# Scheduled via schedule_task.ps1 (LinkedInAutoPoster_Maintenance task).

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "`n[Maintenance] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') — Starting daily maintenance run" -ForegroundColor Cyan

# Pull latest main before doing anything
git pull origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "[Maintenance] git pull failed — aborting." -ForegroundColor Red
    exit 1
}

# Ensure commits are always under your account
git config user.name "TemRevil"
git config user.email "temrevil@gmail.com"

# Check claude CLI is available
$claudePath = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claudePath) {
    Write-Host "[Maintenance] ERROR: claude CLI not found on PATH." -ForegroundColor Red
    Write-Host "  Install it from: https://claude.ai/download" -ForegroundColor Yellow
    exit 1
}

$prompt = @"
You are an autonomous maintenance agent for the LinkedIn Auto-Poster repository
(Python/Flask app; backend modules live in src/, dashboard UI is templates/index.html).

Your job for THIS run: fix exactly ONE issue from the prioritized audit list below,
verify it, commit it, and push to main.

## Rules
- Fix exactly one issue per run — never batch multiple.
- Pick the lowest-numbered issue that is NOT already fixed in the current code.
  Before fixing, open the cited file and confirm the problem still exists.
  If an issue is already resolved, move to the next number.
- Keep changes surgical and minimal. Do not refactor unrelated code.
- Issues #5 and #6 are large architectural refactors — skip them.
- Do NOT modify .gitignore, secrets, or any gitignored data.
- After editing, verify Python changes compile: python -m py_compile <files>.
- Commit message must follow conventional commits naming the issue fixed
  (e.g. fix: guarantee SQLite connections close (audit #2)).
- git config user.name must be "TemRevil" and user.email "temrevil@gmail.com" — run both before committing.
- Then run: git push origin main
- If every issue below is already fixed, make no changes — just print "All audit issues resolved".

## Prioritized audit issues
1. Claude CLI argument order (src/post_generator.py _run_claude_code, and Claude subprocess calls
   in src/approval_server.py). claude -p must receive the prompt via stdin piping with valid flags
   after -p; ensure --allowed-tools / --output-format actually apply and are not swallowed as the prompt.
2. SQLite connection leaks (src/database.py). Functions open a connection and call conn.close() at the
   end, but an exception skips the close. Wrap each in try/finally so the connection always closes.
3. Stale settings import (src/builder_posts.py). GITHUB_USER = get_setting(...) runs at import time,
   so dashboard changes don't apply until restart. Read the setting inside the function that uses it.
4. Headless bot detection (src/linkedin_poster.py). Chromium launches headless with the default bot
   User-Agent. Set a realistic user_agent on new_context(...). Do not change headless mode.
5. (LARGE — skip) Synchronous blocking in Flask routes.
6. (LARGE — skip) Move file-based JSON drafts into a drafts table in discovery.db.
7. clean_connections User-Agent (src/clean_connections.py). The comment says set a realistic user agent
   but only Accept-Language is set. Add a real user_agent to new_context(...).
8. Language-dependent selectors (src/linkedin_poster.py). Buttons matched by exact text (Done, next...).
   Add language-agnostic selectors (ARIA/role/css/type) with text matching as fallback.
9. Destructive humanizer replacements (src/humanizer.py). Phrases like dive into/delve into are replaced
   with empty string, breaking grammar. Replace with a grammatical synonym instead.
10. SQLite connection inside loop (src/news_discovery.py). A new connection is opened/closed per card
    inside a loop. Open one connection before the loop, use a single transaction, close after.
11. Timezone skew (src/news_scraper.py). pub_dt.replace(tzinfo=None) drops timezone without converting.
    Use pub_dt.astimezone().replace(tzinfo=None) instead.
12. Unhandled API errors in UI (templates/index.html, the api() helper). It does
    (await fetch(path,opts)).json() without checking response.ok, so server 500s leave the loader
    spinning forever. Make api() check response.ok, hide the loader, and show an error toast on failure.
"@

Write-Host "[Maintenance] Handing off to Claude Code CLI..." -ForegroundColor Cyan

$prompt | & $claudePath -p `
    --allowed-tools "Read,Write,Edit,Bash" `
    --add-dir "$ScriptDir"

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[Maintenance] Run complete." -ForegroundColor Green
} else {
    Write-Host "`n[Maintenance] Claude exited with code $LASTEXITCODE" -ForegroundColor Yellow
}
