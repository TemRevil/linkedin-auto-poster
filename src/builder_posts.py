"""
Builder Updates — pulls recent GitHub commits from the user's repos
so they can be used as the source material for "Here's what I shipped" posts.

Uses GitHub's unauthenticated REST API (60 reqs/hr per IP — plenty for daily use).
For private repos or higher rate limits, set GITHUB_TOKEN env var.
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from config import BASE_DIR, get_setting

GITHUB_USER = get_setting("github_username", "") or os.environ.get("GITHUB_USERNAME", "")
GITHUB_API = "https://api.github.com"
CACHE_FILE = BASE_DIR / "_github_cache.json"
CACHE_TTL_SECONDS = 1800  # 30 min — don't hammer the API


def _headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "LinkedinAutoPoster/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _load_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        if (datetime.now() - cached_at).total_seconds() < CACHE_TTL_SECONDS:
            return data
    except Exception:
        pass
    return None


def _save_cache(data: dict):
    data["cached_at"] = datetime.now().isoformat()
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def fetch_recent_commits(days: int = 14, force_refresh: bool = False) -> list[dict]:
    """Fetch commits from the user's public repos in the last N days.
    Returns a list of {repo, sha, message, date, url, additions, deletions}."""
    if not GITHUB_USER:
        print("[Builder] No github_username set in settings. Skipping.")
        return []
    if not force_refresh:
        cached = _load_cache()
        if cached and "commits" in cached:
            return cached["commits"]

    commits = []
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # 1) List user's repos
        r = requests.get(
            f"{GITHUB_API}/users/{GITHUB_USER}/repos",
            params={"per_page": 30, "sort": "pushed", "direction": "desc"},
            headers=_headers(),
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[Builder] GitHub repos fetch failed: {r.status_code}")
            return []
        repos = r.json()
    except Exception as e:
        print(f"[Builder] GitHub repos error: {e}")
        return []

    # 2) For each repo, get recent commits
    for repo in repos[:15]:  # Top 15 most recently pushed
        repo_name = repo.get("name", "")
        if not repo_name:
            continue
        if repo.get("fork", False):
            continue  # Skip forks — only original work
        try:
            r = requests.get(
                f"{GITHUB_API}/repos/{GITHUB_USER}/{repo_name}/commits",
                params={"author": GITHUB_USER, "since": since, "per_page": 10},
                headers=_headers(),
                timeout=15,
            )
            if r.status_code != 200:
                continue
            for c in r.json():
                msg = c.get("commit", {}).get("message", "")
                first_line = msg.split("\n", 1)[0].strip()
                # Skip merge commits and trivial automation
                if first_line.lower().startswith(("merge ", "merge:", "wip", "wip:")):
                    continue
                date = c.get("commit", {}).get("author", {}).get("date", "")
                commits.append({
                    "repo": repo_name,
                    "repo_description": repo.get("description", "") or "",
                    "language": repo.get("language", "") or "",
                    "sha": c.get("sha", "")[:8],
                    "message": first_line[:300],
                    "full_message": msg[:1000],
                    "date": date,
                    "url": c.get("html_url", ""),
                })
        except Exception as e:
            print(f"[Builder] commits for {repo_name}: {e}")
            continue

    # Sort by date desc
    commits.sort(key=lambda c: c.get("date", ""), reverse=True)
    _save_cache({"commits": commits})
    print(f"[Builder] Fetched {len(commits)} commits across {len(repos)} repos.")
    return commits


def commit_to_article(commit: dict) -> dict:
    """Convert a commit to an article shape so it can flow through post_generator."""
    repo = commit.get("repo", "unknown")
    repo_desc = commit.get("repo_description", "")
    msg = commit.get("message", "")
    full_msg = commit.get("full_message", "")
    lang = commit.get("language", "")

    title = f"Builder update: {msg}"
    summary_parts = [
        f"Recent commit on my {repo} repo.",
    ]
    if repo_desc:
        summary_parts.append(f"Project context: {repo_desc}")
    if lang:
        summary_parts.append(f"Stack: {lang}.")
    if full_msg and full_msg != msg:
        summary_parts.append(f"Full message: {full_msg}")

    return {
        "title": title,
        "summary": " ".join(summary_parts),
        "link": commit.get("url", ""),
        "source": f"GitHub/{repo}",
        "type": "builder",
        "commit_sha": commit.get("sha", ""),
        "commit_date": commit.get("date", ""),
    }


if __name__ == "__main__":
    commits = fetch_recent_commits(days=14, force_refresh=True)
    print(f"\nMost recent {min(10, len(commits))} commits:\n")
    for c in commits[:10]:
        print(f"  [{c['repo']}] {c['date'][:10]}  {c['message'][:60]}")
