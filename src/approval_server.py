"""
LinkedIn AutoPoster Dashboard — Linkedin-A01
Multi-page SPA with posts, discovery swipe, connections, insights, account.
Supports multi-session management, manual preference editing, and live connection scraping.
"""
import json
import os
import re
import stat
import sys
import asyncio
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, abort

from config import (
    APPROVAL_HOST, APPROVAL_PORT, POSTS_DIR, REMINDER_INTERVAL_MINUTES,
    BASE_DIR, AUTH_DIR, PROFILE_FILE, PREFERENCES_FILE, DISCOVERY_DIR,
    SESSIONS_FILE, MAX_LINKEDIN_SESSIONS, MAX_GMAIL_SESSIONS,
    ANTHROPIC_API_KEY, SETTINGS_FILE, DEFAULT_SETTINGS, save_settings, get_setting,
)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit

CONNECTIONS_FILE = BASE_DIR / "connections.toml"
CONNECTIONS_JSON = BASE_DIR / "connections.json"
INSIGHTS_FILE = BASE_DIR / "audience_insights.json"

_DRAFT_ID_RE = re.compile(r"\A\d{8}_\d{6}\Z")

_CSRF_ALLOWED_ORIGINS = {"http://127.0.0.1:5555", "http://localhost:5555"}


@app.before_request
def _check_origin():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("Origin", "")
        referer = request.headers.get("Referer", "")
        if origin and origin not in _CSRF_ALLOWED_ORIGINS:
            abort(403)
        if not origin and not any(referer.startswith(a) for a in _CSRF_ALLOWED_ORIGINS):
            abort(403)


# Scrape state
_scrape_lock = threading.Lock()
_scrape_running = False
_scrape_result = None


# ─── Session Manager ────────────────────────────────────────────────────────

def _load_sessions_config():
    """Load multi-session config, migrating legacy single files if needed.
    A corrupt/half-written sessions.json is set aside and rebuilt from the
    legacy files rather than 500-ing every account route that reads it."""
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # One bad config file must not take down the whole sessions/account
            # surface (get_system_status and every /api/sessions* route call
            # this). Preserve it for debugging, then fall through and rebuild.
            print(f"[Sessions] sessions.json unreadable ({e}); rebuilding from legacy files.")
            try:
                SESSIONS_FILE.replace(SESSIONS_FILE.with_suffix(".corrupt"))
            except OSError:
                pass

    # Migrate/rebuild: if old linkedin.json exists, create sessions config
    config = {"linkedin": [], "gmail": []}
    legacy_li = AUTH_DIR / "linkedin.json"
    if legacy_li.exists():
        config["linkedin"].append({
            "id": 1, "name": "Main",
            "file": "linkedin.json", "active": True,
            "created_at": datetime.now().isoformat(),
        })
    legacy_gm = AUTH_DIR / "gmail_token.json"
    if legacy_gm.exists():
        config["gmail"].append({
            "id": 1, "name": "Main",
            "file": "gmail_token.json", "active": True,
            "created_at": datetime.now().isoformat(),
        })

    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    _save_sessions_config(config)
    return config


def _save_sessions_config(config):
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def _get_active_session(session_type: str) -> dict | None:
    config = _load_sessions_config()
    for s in config.get(session_type, []):
        if s.get("active"):
            return s
    return None


# ─── Data Helpers ────────────────────────────────────────────────────────────

def _read_draft_file(f):
    """Load one draft JSON, returning None (and logging) on a corrupt/half-written file
    so a single bad draft can't 500 the entire Posts tab."""
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Server] Skipping unreadable draft {f.name}: {e}")
        return None


def get_pending_posts() -> list:
    posts = []
    for f in sorted(POSTS_DIR.glob("draft_*.json"), reverse=True):
        draft = _read_draft_file(f)
        if draft and draft.get("status") == "pending_approval":
            posts.append(draft)
    return posts


def get_history_posts() -> list:
    posts = []
    for f in sorted(POSTS_DIR.glob("draft_*.json"), reverse=True):
        draft = _read_draft_file(f)
        if draft and draft.get("status") != "pending_approval":
            posts.append(draft)
    return posts[:20]


def get_all_posts() -> list:
    posts = []
    for f in sorted(POSTS_DIR.glob("draft_*.json"), reverse=True):
        draft = _read_draft_file(f)
        if draft:
            posts.append(draft)
    return posts


def update_draft(draft_id: str, updates: dict):
    if not _DRAFT_ID_RE.fullmatch(draft_id or ""):
        return None
    for f in POSTS_DIR.glob(f"draft_{draft_id}.json"):
        with open(f, "r", encoding="utf-8") as fp:
            draft = json.load(fp)
        draft.update(updates)
        # Atomic write: temp file + replace so a crash can't corrupt the draft.
        tmp = f.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(draft, fp, indent=2, ensure_ascii=False)
        tmp.replace(f)
        return draft
    return None


def load_draft(draft_id: str):
    """Read a single draft JSON by id (or None)."""
    if not _DRAFT_ID_RE.fullmatch(draft_id or ""):
        return None
    f = POSTS_DIR / f"draft_{draft_id}.json"
    if not f.exists():
        return None
    with open(f, "r", encoding="utf-8") as fp:
        return json.load(fp)


def draft_images(draft: dict) -> list:
    """Normalized list of image file paths for a draft.
    Supports the new image_paths list and the legacy single image_path."""
    if not draft:
        return []
    paths = draft.get("image_paths")
    if isinstance(paths, list):
        out = [p for p in paths if p]
        if out:
            return out
    single = draft.get("image_path")
    return [single] if single else []


def img_url(path: str) -> str:
    """Public URL for a stored image path (served by /image/<filename>)."""
    return "/image/" + Path(path).name if path else ""


MAX_IMAGES_PER_POST = 9  # LinkedIn carousel cap we enforce in the UI


def get_connections() -> list:
    if CONNECTIONS_JSON.exists():
        with open(CONNECTIONS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    if CONNECTIONS_FILE.exists():
        from connections_scraper import load_from_toml
        return load_from_toml(CONNECTIONS_FILE)
    return []


def get_insights() -> dict:
    if INSIGHTS_FILE.exists():
        with open(INSIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_system_status() -> dict:
    config = _load_sessions_config()
    li_active = _get_active_session("linkedin")
    gm_active = _get_active_session("gmail")
    all_posts = get_all_posts()
    gmail_method = get_setting("gmail_method", "none")
    return {
        "linkedin_connected": li_active is not None and (AUTH_DIR / li_active["file"]).exists(),
        "gmail_connected": (gmail_method == "claude_mcp") or (gm_active is not None and (AUTH_DIR / gm_active["file"]).exists()),
        "gmail_method": gmail_method,
        "total_posts": len(all_posts),
        "pending_posts": sum(1 for p in all_posts if p.get("status") == "pending_approval"),
        "approved_posts": sum(1 for p in all_posts if p.get("status") == "approved"),
        "posted_posts": sum(1 for p in all_posts if p.get("status") == "posted"),
        "rejected_posts": sum(1 for p in all_posts if p.get("status") == "rejected"),
        "connections_count": len(get_connections()),
        "last_scrape": get_insights().get("scraped_at", "Never"),
        "linkedin_sessions": len(config.get("linkedin", [])),
        "gmail_sessions": len(config.get("gmail", [])),
    }


# ─── Page Route ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── Post API ────────────────────────────────────────────────────────────────

@app.route("/api/pending")
def api_pending():
    return jsonify({"posts": get_pending_posts()})


@app.route("/api/history")
def api_history():
    return jsonify({"posts": get_history_posts()})


@app.route("/api/all-posts")
def api_all_posts():
    return jsonify({"posts": get_all_posts()})


@app.route("/api/approve", methods=["POST"])
def api_approve():
    """Approve a draft AND immediately push it to LinkedIn via Playwright.
    Body: { id, content, auto_post?: bool=true }"""
    data = request.get_json()
    draft_id = data.get("id")
    content = data.get("content", "")
    auto_post = data.get("auto_post", True)

    updates = {"status": "approved", "approved_at": datetime.now().isoformat()}
    if content:
        updates["post_content"] = content
    draft = update_draft(draft_id, updates)
    if not draft:
        return jsonify({"message": "Draft not found", "status": "error"}), 404

    if not auto_post:
        return jsonify({"message": "Post approved (queued for next post run).", "status": "ok"})

    # Fire posting in background so the dashboard request returns fast
    global _post_running, _post_result
    with _post_lock:
        if _post_running:
            return jsonify({
                "message": "Approved. Another post is currently being submitted — yours will follow.",
                "status": "queued",
            })
        _post_running = True
        _post_result = None
    threading.Thread(target=_run_post_to_linkedin, daemon=True).start()

    return jsonify({
        "message": "Approved. Posting to LinkedIn now…",
        "status": "ok",
    })


# Track in-progress LinkedIn posting
_post_lock = threading.Lock()
_post_running = False
_post_result = None


def _run_post_to_linkedin():
    global _post_running, _post_result
    try:
        from linkedin_poster import post_approved_drafts
        asyncio.run(post_approved_drafts())
        _post_result = {"status": "done", "ended_at": datetime.now().isoformat()}
    except Exception as e:
        import traceback; traceback.print_exc()
        _post_result = {"status": "error", "error": str(e), "ended_at": datetime.now().isoformat()}
    finally:
        with _post_lock:
            _post_running = False


@app.route("/api/post-status")
def api_post_status():
    """Poll for LinkedIn posting status."""
    return jsonify({"running": _post_running, "result": _post_result})


@app.route("/api/post-now", methods=["POST"])
def api_post_now():
    """Manually trigger posting all approved drafts."""
    global _post_running, _post_result
    with _post_lock:
        if _post_running:
            return jsonify({"status": "running", "message": "Already posting"})
        _post_running = True
        _post_result = None
    threading.Thread(target=_run_post_to_linkedin, daemon=True).start()
    return jsonify({"status": "ok", "message": "Posting approved drafts to LinkedIn…"})


@app.route("/api/retry-post", methods=["POST"])
def api_retry_post():
    """Re-attempt a previously failed post. Resets status to 'approved' and
    triggers posting. Caller can poll /api/post-status as usual."""
    global _post_running, _post_result
    data = request.get_json(silent=True) or {}
    draft_id = data.get("id")
    if not draft_id:
        return jsonify({"status": "error", "message": "id is required"}), 400
    draft = update_draft(draft_id, {
        "status": "approved",
        "approved_at": datetime.now().isoformat(),
    })
    if not draft:
        return jsonify({"status": "error", "message": "Draft not found"}), 404
    with _post_lock:
        if _post_running:
            return jsonify({
                "status": "queued",
                "message": "Already posting — this draft will be picked up in the same run.",
            })
        _post_running = True
        _post_result = None
    threading.Thread(target=_run_post_to_linkedin, daemon=True).start()
    return jsonify({"status": "ok", "message": "Retrying post to LinkedIn…"})


@app.route("/diag-screenshot/<filename>")
def serve_diag_screenshot(filename):
    """Serve diagnostic error screenshots saved in posts/ so the UI can show
    them next to failed drafts."""
    from flask import send_from_directory, abort
    if "/" in filename or "\\" in filename or ".." in filename:
        abort(400)
    if not filename.startswith(("error_", "image_fail_")) or not filename.endswith(".png"):
        abort(404)  # Only diagnostic pngs
    file_path = POSTS_DIR / filename
    if not file_path.exists():
        abort(404)
    return send_from_directory(str(POSTS_DIR), filename)


def _archive_draft_images(draft: dict) -> int:
    """Move a draft's attached images into posts/images/rejected/ so they're
    not left orphaned in the active images folder. Returns count moved."""
    import shutil
    if not draft:
        return 0
    images_dir = (POSTS_DIR / "images").resolve()
    rejected_dir = POSTS_DIR / "images" / "rejected"
    moved = 0
    for p in draft_images(draft):
        try:
            src = Path(p).resolve()
            if not str(src).startswith(str(images_dir)):
                continue
            if src.exists():
                rejected_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(rejected_dir / src.name))
                moved += 1
        except Exception as e:
            print(f"[Reject] Could not archive image {p}: {e}")
    return moved


@app.route("/api/reject", methods=["POST"])
def api_reject():
    data = request.get_json()
    draft_id = data.get("id")
    reason = (data.get("reason") or "").strip()

    # Move any attached images out of the active folder on rejection.
    existing = load_draft(draft_id)
    moved = _archive_draft_images(existing)

    updates = {"status": "rejected", "rejected_at": datetime.now().isoformat()}
    if moved:
        updates["image_paths"] = []
        updates["image_path"] = None
    if reason:
        updates["rejection_reason"] = reason
    update_draft(draft_id, updates)

    # Save reason to DB so next generation reads it as feedback
    if reason:
        try:
            import database as db
            db.save_rejection_reason(draft_id, reason)
        except Exception as e:
            print(f"[Reject] Could not save reason to DB: {e}")

    return jsonify({"message": "Post rejected.", "status": "ok"})


@app.route("/api/edit", methods=["POST"])
def api_edit():
    data = request.get_json()
    draft_id = data.get("id")
    content = data.get("content", "")
    update_draft(draft_id, {"post_content": content})
    return jsonify({"message": "Edits saved.", "status": "ok"})


@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    """Upload one or more images and APPEND them to a draft's image list.
    Accepts a single `file` or multiple `file` parts in one request."""
    draft_id = request.form.get("draft_id")
    if not draft_id:
        return jsonify({"status": "error", "message": "Missing draft_id"}), 400

    files = request.files.getlist("file")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400

    draft = load_draft(draft_id)
    if draft is None:
        return jsonify({"status": "error", "message": "Draft not found"}), 404

    allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    images_dir = POSTS_DIR / "images"
    images_dir.mkdir(exist_ok=True)

    current = draft_images(draft)
    added = 0
    for f in files:
        if len(current) >= MAX_IMAGES_PER_POST:
            break
        ext = Path(f.filename).suffix.lower()
        if ext not in allowed:
            continue
        # Unique per-image filename: <draft_id>_<microsecond stamp><ext>
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        fname = f"{draft_id}_{stamp}{ext}"
        dest = images_dir / fname
        f.save(str(dest))
        current.append(str(dest))
        added += 1

    if added == 0:
        return jsonify({"status": "error", "message": "No valid images (allowed: png, jpg, jpeg, gif, webp)"}), 400

    update_draft(draft_id, {
        "image_paths": current,
        "image_path": current[0] if current else None,  # back-compat
        "needs_image": True,
    })
    capped = len(current) >= MAX_IMAGES_PER_POST
    return jsonify({
        "status": "ok",
        "message": f"Added {added} image{'s' if added != 1 else ''}." + (" (max reached)" if capped else ""),
        "image_paths": current,
        "image_urls": [img_url(p) for p in current],
    })


@app.route("/api/remove-image", methods=["POST"])
def api_remove_image():
    """Remove one image (by `path`/filename) from a draft, or all if omitted."""
    data = request.get_json() or {}
    draft_id = data.get("id")
    target = data.get("path")  # filename or full path; None → remove everything

    draft = load_draft(draft_id)
    if draft is None:
        return jsonify({"status": "error", "message": "Draft not found"}), 404

    imgs = draft_images(draft)
    images_dir = (POSTS_DIR / "images").resolve()
    if target:
        target_name = Path(target).name
        kept = []
        for p in imgs:
            if Path(p).name == target_name:
                try:
                    resolved = Path(p).resolve()
                    if str(resolved).startswith(str(images_dir)):
                        resolved.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                kept.append(p)
        imgs = kept
        msg = "Image removed."
    else:
        for p in imgs:
            try:
                resolved = Path(p).resolve()
                if str(resolved).startswith(str(images_dir)):
                    resolved.unlink(missing_ok=True)
            except Exception:
                pass
        imgs = []
        msg = "All images removed."

    update_draft(draft_id, {
        "image_paths": imgs,
        "image_path": imgs[0] if imgs else None,
    })
    return jsonify({
        "status": "ok",
        "message": msg,
        "image_paths": imgs,
        "image_urls": [img_url(p) for p in imgs],
    })


@app.route("/image/<filename>")
def serve_image(filename):
    """Serve uploaded images so the dashboard can preview them."""
    from flask import send_from_directory, abort
    images_dir = POSTS_DIR / "images"
    # Only allow safe filenames (no path traversal)
    if "/" in filename or "\\" in filename or ".." in filename:
        abort(400)
    file_path = images_dir / filename
    if not file_path.exists():
        abort(404)
    return send_from_directory(str(images_dir), filename)


# ─── Gmail credentials upload ────────────────────────────────────────────────

@app.route("/api/upload-credentials", methods=["POST"])
def api_upload_credentials():
    """Upload a Google OAuth credentials.json file for Gmail integration."""
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"status": "error", "message": "Empty file"}), 400

    # Validate it's valid JSON with required OAuth fields
    try:
        content = f.read()
        data = json.loads(content)
        # Google OAuth credentials.json has either "installed" or "web" key
        if "installed" not in data and "web" not in data:
            return jsonify({
                "status": "error",
                "message": "Invalid credentials file. Must be a Google OAuth 2.0 client JSON (should have an 'installed' or 'web' key)."
            }), 400
    except json.JSONDecodeError:
        return jsonify({"status": "error", "message": "File is not valid JSON"}), 400

    # Save to project root
    creds_path = BASE_DIR / "credentials.json"
    with open(creds_path, "wb") as out:
        out.write(content)
    if os.name == "posix":
        os.chmod(creds_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

    return jsonify({
        "status": "ok",
        "message": "credentials.json saved. You can now add a Gmail session and click Login to complete OAuth."
    })


# ─── Connections API ─────────────────────────────────────────────────────────

@app.route("/api/connections")
def api_connections():
    connections = get_connections()
    search = request.args.get("search", "").lower()
    category = request.args.get("category", "all")

    if search:
        connections = [c for c in connections if
                       search in c.get("name", "").lower() or
                       search in c.get("headline", "").lower() or
                       search in c.get("location", "").lower()]

    if category != "all":
        kw_map = {
            "developers": ["developer", "engineer", "programmer", "frontend", "backend",
                          "fullstack", "full-stack", "full stack", "software", "react",
                          "angular", "node", "python", "java", "devops", ".net", "flutter"],
            "designers": ["designer", "ux", "ui", "product design", "graphic", "creative"],
            "recruiters": ["recruiter", "talent", "hiring", "hr", "human resources"],
            "founders": ["founder", "ceo", "co-founder", "entrepreneur", "owner", "cto"],
            "managers": ["manager", "lead", "director", "head of", "vp", "principal"],
            "students": ["student", "intern", "trainee", "fresh grad", "junior"],
        }
        words = kw_map.get(category, [])
        if words:
            connections = [c for c in connections if
                          any(w in (c.get("headline", "") + " " + c.get("name", "")).lower()
                              for w in words)]

    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 30))))
    except (TypeError, ValueError):
        page, per_page = 1, 30
    total = len(connections)
    start = (page - 1) * per_page

    return jsonify({
        "connections": connections[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


# ─── Scrape Connections (live trigger) ───────────────────────────────────────

@app.route("/api/scrape-connections", methods=["POST"])
def api_scrape_connections():
    global _scrape_running, _scrape_result
    with _scrape_lock:
        if _scrape_running:
            return jsonify({"message": "Scrape already in progress", "status": "running"})
        _scrape_running = True
        _scrape_result = None

    threading.Thread(target=_run_scrape, daemon=True).start()
    return jsonify({"message": "Scrape started! This may take a few minutes.", "status": "ok"})


def _run_scrape():
    global _scrape_running, _scrape_result
    try:
        from connections_scraper import scrape_connections, analyze_audience
        conns = asyncio.run(scrape_connections())
        if conns:
            analyze_audience(conns)
        _scrape_result = {"count": len(conns), "status": "done"}
    except Exception as e:
        _scrape_result = {"error": str(e), "status": "error"}
    finally:
        with _scrape_lock:
            _scrape_running = False


@app.route("/api/scrape-status")
def api_scrape_status():
    return jsonify({"running": _scrape_running, "result": _scrape_result})


@app.route("/api/clean-connections", methods=["POST"])
def api_clean_connections():
    """Run the offline regex cleaner on connections.json."""
    try:
        from clean_connections import clean_connections_data
        clean_connections_data()
        # Count how many were fixed (compare backup vs clean)
        if CONNECTIONS_JSON.exists():
            with open(CONNECTIONS_JSON, "r", encoding="utf-8") as f:
                cleaned = json.load(f)
            return jsonify({
                "message": f"Cleaned {len(cleaned)} connections",
                "status": "ok",
                "fixed": len(cleaned),
            })
        return jsonify({"message": "No connections file found", "status": "error"}), 404
    except Exception as e:
        return jsonify({"message": str(e), "status": "error"}), 500


@app.route("/api/deep-scrape", methods=["POST"])
def api_deep_scrape():
    """Start per-profile Playwright scrape in background."""
    global _scrape_running, _scrape_result
    with _scrape_lock:
        if _scrape_running:
            return jsonify({"message": "A scrape is already in progress", "status": "running"})
        _scrape_running = True
        _scrape_result = None

    def _run_deep():
        global _scrape_running, _scrape_result
        try:
            from clean_connections import scrape_profiles
            asyncio.run(scrape_profiles())
            if CONNECTIONS_JSON.exists():
                with open(CONNECTIONS_JSON, "r", encoding="utf-8") as f:
                    count = len(json.load(f))
            else:
                count = 0
            _scrape_result = {"count": count, "status": "done"}
        except Exception as e:
            _scrape_result = {"error": str(e), "status": "error"}
        finally:
            with _scrape_lock:
                _scrape_running = False

    threading.Thread(target=_run_deep, daemon=True).start()
    return jsonify({
        "message": "Deep scrape started — visiting each profile page. This may take a while.",
        "status": "ok",
    })


# ─── Insights / Status API ──────────────────────────────────────────────────

@app.route("/api/insights")
def api_insights():
    return jsonify(get_insights())


@app.route("/api/reanalyze-audience", methods=["POST"])
def api_reanalyze_audience():
    """Re-run audience analysis on existing connections.json without re-scraping."""
    try:
        from connections_scraper import analyze_audience
        insights = analyze_audience()
        return jsonify({
            "status": "ok",
            "total_connections": insights.get("total_connections", 0),
            "top_keywords_count": len(insights.get("top_titles", [])),
            "message": "Audience insights regenerated from existing connections data.",
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/status")
def api_status():
    return jsonify(get_system_status())


# ─── Sessions API (multi-session) ───────────────────────────────────────────

@app.route("/api/sessions")
def api_sessions():
    config = _load_sessions_config()
    sessions = []
    for stype in ("linkedin", "gmail"):
        for s in config.get(stype, []):
            file_path = AUTH_DIR / s["file"]
            sessions.append({
                "id": s["id"],
                "type": stype,
                "name": s.get("name", "Untitled"),
                "file": s["file"],
                "active": s.get("active", False),
                "exists": file_path.exists(),
                "size_kb": round(file_path.stat().st_size / 1024, 1) if file_path.exists() else 0,
                "created_at": s.get("created_at", ""),
            })
    return jsonify({"sessions": sessions})


@app.route("/api/sessions/login", methods=["POST"])
def api_sessions_login():
    """Create a new (empty) session entry.
    The user must then click 'Login' on that entry to authenticate."""
    data = request.get_json()
    stype = data.get("type", "linkedin")
    name = data.get("name", "Untitled").strip() or "Untitled"

    # Gmail: check for credentials.json BEFORE creating the session entry
    if stype == "gmail":
        creds_file = BASE_DIR / "credentials.json"
        if not creds_file.exists():
            return jsonify({
                "status": "setup_required",
                "message": "Gmail requires a Google Cloud OAuth setup first.",
                "setup_steps": [
                    "Go to https://console.cloud.google.com/",
                    "Create a project (e.g. 'LinkedIn AutoPoster')",
                    "Enable the Gmail API: APIs & Services > Library > 'Gmail API' > Enable",
                    "Create OAuth credentials: APIs & Services > Credentials > Create > OAuth 2.0 Client ID > Desktop app",
                    "Download the JSON file",
                    f"Rename it to 'credentials.json' and place it in: {BASE_DIR}",
                    "Then click 'Add Gmail Session' again.",
                ],
                "note": "This is optional! The system works fine without Gmail by using RSS feeds and web scraping.",
            })

    config = _load_sessions_config()
    max_s = MAX_LINKEDIN_SESSIONS if stype == "linkedin" else MAX_GMAIL_SESSIONS
    current = config.get(stype, [])

    if len(current) >= max_s:
        return jsonify({"message": f"Maximum {max_s} {stype} sessions reached", "status": "error"}), 400

    # Generate new ID and filename
    existing_ids = [s["id"] for s in current]
    new_id = max(existing_ids, default=0) + 1
    filename = f"{stype}_{new_id}.json" if new_id > 1 else f"{stype}.json"

    # Add session entry (NOT auto-active, NOT auto-login — user chooses next)
    entry = {
        "id": new_id, "name": name, "file": filename,
        "active": len(current) == 0,
        "created_at": datetime.now().isoformat(),
    }
    config.setdefault(stype, []).append(entry)
    _save_sessions_config(config)

    return jsonify({
        "message": f"{name} added. Click Login to authenticate.",
        "status": "ok", "session_id": new_id,
    })


# Track in-progress logins so the UI can poll status
_login_progress = {}  # {filename: {"status": "in_progress"|"done"|"error", "started_at": ...}}


@app.route("/api/sessions/authenticate", methods=["POST"])
def api_sessions_authenticate():
    """Trigger the login/OAuth flow for an EXISTING session entry."""
    data = request.get_json()
    stype = data.get("type", "linkedin")
    sid = data.get("id")
    config = _load_sessions_config()
    entries = config.get(stype, [])
    entry = next((s for s in entries if s["id"] == sid), None)
    if not entry:
        return jsonify({"status": "error", "message": "Session not found"}), 404

    filename = entry["file"]

    if stype == "linkedin":
        if _login_progress.get(filename, {}).get("status") == "in_progress":
            return jsonify({"status": "running", "message": "Login already in progress"})
        _login_progress[filename] = {
            "status": "in_progress",
            "started_at": datetime.now().isoformat(),
        }
        threading.Thread(
            target=_do_linkedin_login, args=(filename,), daemon=True
        ).start()
        return jsonify({
            "status": "ok",
            "message": "A browser window opened. Log in to LinkedIn — the session saves automatically once you reach the feed.",
        })

    if stype == "gmail":
        # Check if credentials.json exists — if not, return setup instructions
        creds_file = BASE_DIR / "credentials.json"
        if not creds_file.exists():
            return jsonify({
                "status": "setup_required",
                "message": "Gmail requires a Google Cloud OAuth setup first.",
                "setup_steps": [
                    "Go to https://console.cloud.google.com/",
                    "Create a project (e.g. 'LinkedIn AutoPoster')",
                    "Enable the Gmail API: APIs & Services > Library > 'Gmail API' > Enable",
                    "Create OAuth credentials: APIs & Services > Credentials > Create > OAuth 2.0 Client ID > Desktop app",
                    "Download the JSON file",
                    f"Rename it to 'credentials.json' and place it in: {BASE_DIR}",
                    "Then click Login again to complete the OAuth flow.",
                ],
                "note": "This is optional! The system works fine without Gmail by using RSS feeds and web scraping.",
            })

        if _login_progress.get(filename, {}).get("status") == "in_progress":
            return jsonify({"status": "running", "message": "Login already in progress"})
        _login_progress[filename] = {
            "status": "in_progress",
            "started_at": datetime.now().isoformat(),
        }
        threading.Thread(
            target=_do_gmail_login, args=(filename,), daemon=True
        ).start()
        return jsonify({
            "status": "ok",
            "message": "A browser tab will open for Google OAuth. Grant access and you'll be redirected back.",
        })

    return jsonify({"status": "error", "message": "Unknown session type"}), 400


@app.route("/api/sessions/auth-status")
def api_sessions_auth_status():
    """Report on any in-progress logins so the UI can show spinners."""
    return jsonify({"logins": dict(_login_progress)})


def _do_gmail_login(filename: str):
    """Run the Gmail OAuth flow, save token to AUTH_DIR / filename."""
    _log_session(f"[Gmail login] Starting for filename={filename}")
    try:
        # Double-check credentials.json exists (the endpoint should block before
        # reaching here, but just in case)
        creds_file = BASE_DIR / "credentials.json"
        if not creds_file.exists():
            _login_progress[filename] = {
                "status": "error",
                "error": "credentials.json not found. Complete the Google Cloud setup first.",
                "ended_at": datetime.now().isoformat(),
            }
            _log_session("[Gmail login] Aborted: credentials.json missing")
            return

        import subprocess

        # Run gmail_setup.py with --output flag to specify token filename
        setup_script = BASE_DIR / "src" / "gmail_setup.py"
        if not setup_script.exists():
            raise FileNotFoundError(f"gmail_setup.py not found at {setup_script}")

        proc = subprocess.run(
            [sys.executable, str(setup_script), "--output", filename],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
        _log_session(f"[Gmail login] exit={proc.returncode} stdout={proc.stdout[:300]} stderr={proc.stderr[:300]}")

        # Check if the token file was actually created (not just exit-0)
        token_path = AUTH_DIR / filename
        if proc.returncode == 0 and token_path.exists():
            _login_progress[filename] = {
                "status": "done", "ended_at": datetime.now().isoformat(),
            }
            _log_session(f"[Gmail login] Saved: {filename}")
        else:
            # Parse error: if stdout contains "GMAIL SETUP" it means the script
            # printed instructions instead of running OAuth (shouldn't happen with
            # our endpoint check, but handle gracefully)
            raw = proc.stderr or proc.stdout or ""
            if "GMAIL SETUP" in raw or "credentials.json" in raw:
                err = "credentials.json not found. Complete the Google Cloud setup first."
            else:
                err = raw[:300] or "Token file not created after OAuth flow."
            _login_progress[filename] = {
                "status": "error", "error": err,
                "ended_at": datetime.now().isoformat(),
            }
            _log_session(f"[Gmail login] Error: {err}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _login_progress[filename] = {
            "status": "error", "error": str(e),
            "ended_at": datetime.now().isoformat(),
        }
        _log_session(f"[Gmail login] EXCEPTION: {e}\n{tb}")


def _fix_double_encoded_utf8(text: str) -> str:
    """Fix text where UTF-8 bytes were misinterpreted as Windows CP1252.
    E.g. em-dash — (bytes E2 80 94) read as CP1252 becomes â€" (3 chars).
    Encoding back to CP1252 recovers the original UTF-8 bytes."""
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _split_post_and_refs(raw: str) -> tuple[str, list[str]]:
    """Split Claude output into (post_text, list_of_gmail_ref_lines).

    Expected tail format (only when Gmail MCP is on):

        <post body>

        ---REFS---
        gmail: <subject> | <sender> | <date>
        gmail: ...
    """
    if not raw:
        return "", []
    # Match the marker tolerantly: 3+ dashes, optional spaces, "REFS", any case
    # (Claude often normalizes "---REFS---" to "--- REFS ---" or varies casing).
    marker_re = re.compile(r"-{3,}\s*REFS\s*-{3,}", re.IGNORECASE)
    matches = list(marker_re.finditer(raw))
    if not matches:
        return raw.strip(), []
    last = matches[-1]  # split on the last marker, matching prior rpartition behavior
    body, tail = raw[:last.start()], raw[last.end():]
    refs: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if line.lower().startswith("gmail:"):
            refs.append(line[len("gmail:"):].strip())
    return body.strip(), refs


def _log_session(msg: str):
    """Append a line to the session login log (visible regardless of stdout state)."""
    log_file = BASE_DIR / "logs" / "sessions.log"
    log_file.parent.mkdir(exist_ok=True)
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass
    print(msg, flush=True)


def _do_linkedin_login(filename: str):
    """Run the Playwright login flow for LinkedIn (opens visible browser)."""
    _log_session(f"[LinkedIn login] Starting for filename={filename}")
    try:
        import asyncio as _aio
        from playwright.async_api import async_playwright

        async def _login():
            _log_session("[LinkedIn login] Importing playwright...")
            async with async_playwright() as p:
                _log_session("[LinkedIn login] Launching Chromium (headless=False)...")
                browser = await p.chromium.launch(headless=False)
                _log_session("[LinkedIn login] Chromium launched.")
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto("https://www.linkedin.com/login")
                _log_session(f"[LinkedIn login] Page open — waiting up to 5 min for /feed/...")
                try:
                    await page.wait_for_url("**/feed/**", timeout=300000)
                except Exception as we:
                    _log_session(f"[LinkedIn login] wait_for_url exception: {we}")
                final_url = page.url
                _log_session(f"[LinkedIn login] Final URL: {final_url}")
                if "/feed" in final_url:
                    await ctx.storage_state(path=str(AUTH_DIR / filename))
                    _log_session(f"[LinkedIn login] Session saved: {filename}")
                    _login_progress[filename] = {
                        "status": "done", "ended_at": datetime.now().isoformat(),
                    }
                else:
                    _log_session("[LinkedIn login] Login timed out or was cancelled.")
                    _login_progress[filename] = {
                        "status": "error",
                        "error": "Login timed out or cancelled",
                        "ended_at": datetime.now().isoformat(),
                    }
                await browser.close()

        _aio.run(_login())
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _log_session(f"[LinkedIn login] EXCEPTION: {e}\n{tb}")
        _login_progress[filename] = {
            "status": "error", "error": str(e),
            "ended_at": datetime.now().isoformat(),
        }


@app.route("/api/sessions/remove", methods=["POST"])
def api_sessions_remove():
    data = request.get_json()
    stype = data.get("type", "linkedin")
    sid = data.get("id")

    config = _load_sessions_config()
    sessions = config.get(stype, [])
    target = next((s for s in sessions if s["id"] == sid), None)

    if not target:
        return jsonify({"message": "Session not found", "status": "error"}), 404

    # Delete the file
    file_path = AUTH_DIR / target["file"]
    if file_path.exists():
        file_path.unlink()

    # Remove from config
    config[stype] = [s for s in sessions if s["id"] != sid]

    # If removed session was active, activate the first remaining one
    remaining = config[stype]
    if remaining and not any(s.get("active") for s in remaining):
        remaining[0]["active"] = True

    _save_sessions_config(config)
    return jsonify({"message": f"Session '{target['name']}' removed", "status": "ok"})


@app.route("/api/sessions/rename", methods=["POST"])
def api_sessions_rename():
    data = request.get_json()
    stype = data.get("type", "linkedin")
    sid = data.get("id")
    new_name = data.get("name", "").strip()

    if not new_name:
        return jsonify({"message": "Name cannot be empty", "status": "error"}), 400

    config = _load_sessions_config()
    for s in config.get(stype, []):
        if s["id"] == sid:
            s["name"] = new_name
            _save_sessions_config(config)
            return jsonify({"message": f"Renamed to '{new_name}'", "status": "ok"})

    return jsonify({"message": "Session not found", "status": "error"}), 404


@app.route("/api/sessions/activate", methods=["POST"])
def api_sessions_activate():
    data = request.get_json()
    stype = data.get("type", "linkedin")
    sid = data.get("id")

    config = _load_sessions_config()
    found = False
    for s in config.get(stype, []):
        s["active"] = (s["id"] == sid)
        if s["id"] == sid:
            found = True

    if not found:
        return jsonify({"message": "Session not found", "status": "error"}), 404

    _save_sessions_config(config)
    return jsonify({"message": "Session activated", "status": "ok"})


# ─── Preferences API ────────────────────────────────────────────────────────

@app.route("/api/preferences")
def api_preferences():
    if PREFERENCES_FILE.exists():
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({
        "liked_keywords": {}, "disliked_keywords": {},
        "liked_sources": {}, "disliked_sources": {},
        "history": [], "total_liked": 0, "total_disliked": 0,
    })


@app.route("/api/preferences/add", methods=["POST"])
def api_preferences_add():
    """Manually add a preference keyword."""
    data = request.get_json(silent=True) or {}
    keyword = data.get("keyword", "").strip().lower()
    ptype = data.get("type", "liked")  # "liked" or "disliked"

    if not keyword:
        return jsonify({"message": "Keyword cannot be empty", "status": "error"}), 400

    # Persist to the SQLite swipe store (load/save_preferences is a read-only
    # view now — save_preferences is a no-op — so writing there did nothing).
    action = "liked" if ptype == "liked" else "disliked"
    import database as db
    if not db.set_manual_keyword(keyword, action):
        return jsonify({"message": "Could not save keyword", "status": "error"}), 400

    return jsonify({"message": f"Added '{keyword}' to {ptype}", "status": "ok"})


@app.route("/api/preferences/delete", methods=["POST"])
def api_preferences_delete():
    """Remove a manually-added preference keyword."""
    data = request.get_json(silent=True) or {}
    keyword = data.get("keyword", "").strip().lower()

    if not keyword:
        return jsonify({"message": "Keyword cannot be empty", "status": "error"}), 400

    import database as db
    if db.remove_manual_keyword(keyword):
        return jsonify({"message": f"Removed '{keyword}'", "status": "ok"})

    return jsonify({
        "message": "No manually-added keyword by that name (learned keywords come from swipes).",
        "status": "error",
    }), 404


# ─── Discovery API ───────────────────────────────────────────────────────────

@app.route("/api/discovery")
def api_discovery():
    from news_discovery import get_pending_discovery, get_discovery_stats
    cards = get_pending_discovery()
    stats = get_discovery_stats()
    return jsonify({"cards": cards, "stats": stats})


@app.route("/api/discovery-stats")
def api_discovery_stats():
    from news_discovery import get_discovery_stats
    return jsonify(get_discovery_stats())


@app.route("/api/swipe", methods=["POST"])
def api_swipe():
    from news_discovery import record_swipe
    data = request.get_json()
    card_id = data.get("id")
    action = data.get("action")  # "liked" or "disliked"

    if not card_id or action not in ("liked", "disliked"):
        return jsonify({"message": "Invalid request", "status": "error"}), 400

    success = record_swipe(card_id, action)
    if success:
        return jsonify({"message": f"Card {action}!", "status": "ok"})
    return jsonify({"message": "Card not found", "status": "error"}), 404


# ─── On-Demand Discovery ───────────────────────────────────────────────────

_discover_lock = threading.Lock()
_discover_running = False


@app.route("/api/discover-now", methods=["POST"])
def api_discover_now():
    """Trigger news discovery on demand — scrapes news and generates swipe cards."""
    global _discover_running
    with _discover_lock:
        if _discover_running:
            return jsonify({"message": "Discovery already running", "status": "running"})
        _discover_running = True

    try:
        from news_discovery import discover_news
        cards = asyncio.run(discover_news())
        exploration = sum(1 for c in cards if c.get("is_exploration"))
        return jsonify({
            "message": f"Discovered {len(cards)} cards ({exploration} exploration)",
            "status": "ok",
            "count": len(cards),
            "exploration": exploration,
        })
    except Exception as e:
        return jsonify({"message": f"Discovery failed: {str(e)}", "status": "error"}), 500
    finally:
        with _discover_lock:
            _discover_running = False


# ─── On-Demand Post Generation (Claude) ───────────────────────────────────

_generate_lock = threading.Lock()
_generate_running = False
_generate_result = None
_generate_steps = []      # Live progress steps [{icon, text, ts}]


def _gen_step(icon: str, text: str):
    """Push a progress step visible to the UI via /api/generate-status."""
    _generate_steps.append({
        "icon": icon,
        "text": text,
        "ts": datetime.now().isoformat(),
    })
    print(f"[Generate] {icon} {text}")


@app.route("/api/suggest-topics")
def api_suggest_topics():
    """Return top 3 topic suggestions the user can pick from before generating."""
    try:
        import database as db
        from post_generator import (
            load_audience_insights, _rank_by_audience, _card_to_article, strip_html
        )

        audience = load_audience_insights()
        suggestions = []

        # Priority 1: today's liked cards
        liked = db.get_today_liked_cards()
        if liked:
            ranked = sorted(liked, key=lambda c: c.get("preference_score", 0), reverse=True)
            for card in ranked[:5]:
                suggestions.append(_card_to_article(card))

        # Priority 2: today's pending (un-swiped) high-score cards
        if len(suggestions) < 3:
            pending = db.get_today_pending_cards()
            if pending:
                ranked = sorted(pending, key=lambda c: c.get("preference_score", 0), reverse=True)
                for card in ranked:
                    art = _card_to_article(card)
                    # Avoid duplicates
                    if not any(s.get("title") == art.get("title") for s in suggestions):
                        suggestions.append(art)
                    if len(suggestions) >= 5:
                        break

        # Strip HTML from summaries for clean display
        for s in suggestions:
            s["summary"] = strip_html(s.get("summary", ""))[:200]

        return jsonify({"status": "ok", "topics": suggestions[:3]})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "topics": [], "error": str(e)})


@app.route("/api/generate-post", methods=["POST"])
def api_generate_post():
    """Trigger Claude Code CLI post generation on demand.
    Body: {
        post_type: "news"|"builder"|"hot_take"|"research"|"audience_hook",
        commit_sha?: "...",
        topic?: { title, summary, link, source },   // chosen from suggestions
        custom_topic?: "free text description"       // user typed their own
    }
    """
    global _generate_running, _generate_result
    data = request.get_json(silent=True) or {}
    post_type = data.get("post_type", get_setting("default_post_type", "news"))
    commit_sha = data.get("commit_sha")  # Optional — for builder posts
    chosen_topic = data.get("topic")      # Pre-selected article dict
    custom_topic = data.get("custom_topic", "").strip()  # Free-text topic

    with _generate_lock:
        if _generate_running:
            return jsonify({"message": "Generation already running", "status": "running"})
        _generate_running = True
        _generate_result = None
        _generate_steps.clear()

    threading.Thread(
        target=_run_generate,
        kwargs={
            "post_type": post_type,
            "commit_sha": commit_sha,
            "chosen_topic": chosen_topic,
            "custom_topic": custom_topic,
        },
        daemon=True,
    ).start()
    return jsonify({
        "message": f"Generating {post_type} post with Claude Code... This may take a moment.",
        "status": "ok",
    })


def _run_generate(post_type: str = "news", commit_sha: str = None,
                   chosen_topic: dict = None, custom_topic: str = ""):
    global _generate_running, _generate_result
    try:
        from post_generator import generate_post_draft, load_profile

        # Inject progress callback into post_generator so it can report steps
        import post_generator
        post_generator._progress_callback = _gen_step

        _gen_step("profile", "Loading your voice profile...")
        profile = load_profile()

        # Path A: User typed a custom topic
        if custom_topic:
            _gen_step("topic", f"Custom topic: {custom_topic[:60]}")
            article = {
                "title": custom_topic[:120],
                "summary": custom_topic,
                "link": "",
                "source": "Custom topic",
                "type": "custom",
            }
            draft = generate_post_draft(article, profile, post_type=post_type)

        # Path B: User picked a specific topic from suggestions
        elif chosen_topic and chosen_topic.get("title"):
            _gen_step("topic", f"Topic: {chosen_topic['title'][:60]}")
            draft = generate_post_draft(chosen_topic, profile, post_type=post_type)

        # Path C: Builder posts use GitHub commits
        elif post_type == "builder":
            _gen_step("github", "Fetching your recent GitHub commits...")
            from builder_posts import fetch_recent_commits, commit_to_article

            commits = fetch_recent_commits(days=14)
            if not commits:
                _gen_step("error", "No commits found on GitHub")
                _generate_result = {
                    "status": "error",
                    "error": "No recent commits found on GitHub. Push some code first or check your username.",
                }
                return

            chosen = None
            if commit_sha:
                for c in commits:
                    if c.get("sha", "").startswith(commit_sha):
                        chosen = c
                        break
            if not chosen:
                chosen = commits[0]

            _gen_step("github", f"Using commit: {chosen.get('message', '')[:50]}")
            article = commit_to_article(chosen)
            draft = generate_post_draft(article, profile, post_type="builder")

        # Path D: Auto-pick best topic from discovery data
        else:
            _gen_step("search", "Finding the best topic from your news feed...")
            from scheduler import generate_now
            draft = asyncio.run(generate_now(post_type=post_type))

        if draft:
            _gen_step("done", "Post ready for review!")
            # Save generation steps as references into the draft
            update_draft(draft["id"], {"references": list(_generate_steps)})
            _generate_result = {
                "status": "done",
                "draft_id": draft["id"],
                "title": draft["article"].get("title", ""),
                "generated_by": draft.get("generated_by", "template"),
                "post_type": draft.get("post_type", post_type),
                "preview": draft["post_content"][:200],
            }
        else:
            _gen_step("error", "No suitable article found")
            _generate_result = {
                "status": "error",
                "error": "No suitable article found. Try discovering news first.",
            }
    except Exception as e:
        import traceback
        traceback.print_exc()
        _gen_step("error", f"Error: {str(e)[:80]}")
        _generate_result = {"status": "error", "error": str(e)}
    finally:
        import post_generator as _pg
        _pg._progress_callback = None
        with _generate_lock:
            _generate_running = False


@app.route("/api/generate-status")
def api_generate_status():
    """Check status of on-demand post generation, with live progress steps."""
    return jsonify({
        "running": _generate_running,
        "result": _generate_result,
        "steps": list(_generate_steps),
    })


# ─── Chat-based Generation ─────────────────────────────────────────────────

@app.route("/api/chat-generate", methods=["POST"])
def api_chat_generate():
    """Generate a post from a free-form chat message.
    Body: { message: "Write a hot take about MCP, keep it short" }
    The backend infers post_type, length, and topic from the message,
    then generates using the configured engine (Claude Code CLI or Anthropic API).
    """
    global _generate_running, _generate_result
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"status": "error", "message": "Message is empty"}), 400

    with _generate_lock:
        if _generate_running:
            return jsonify({"message": "Generation already running", "status": "running"})
        _generate_running = True
        _generate_result = None
        _generate_steps.clear()

    engine = get_setting("gen_engine", "claude_code")
    threading.Thread(
        target=_run_chat_generate,
        kwargs={"message": message, "engine": engine},
        daemon=True,
    ).start()
    return jsonify({
        "message": f"Generating post via {engine.replace('_', ' ').title()}...",
        "status": "ok",
        "engine": engine,
    })


def _run_chat_generate(message: str, engine: str = "claude_code"):
    """Background: generate a post from a free-form user message."""
    global _generate_running, _generate_result
    try:
        import post_generator
        post_generator._progress_callback = _gen_step

        _gen_step("profile", "Loading your voice profile...")
        from post_generator import load_profile
        profile = load_profile()
        profile_md = ""
        if PROFILE_FILE.exists():
            profile_md = PROFILE_FILE.read_text(encoding="utf-8")

        # Load past posts for voice calibration
        past_posts = ""
        past_posts_file = BASE_DIR / "my_past_posts.md"
        if past_posts_file.exists():
            past_posts = past_posts_file.read_text(encoding="utf-8")
        time.sleep(0.6)  # Give the UI a beat so each fast step is visible

        _gen_step("topic", f"Topic: {message[:80]}")
        time.sleep(0.6)

        if engine == "anthropic_api":
            draft = _generate_via_anthropic_api(message, profile_md, past_posts)
        else:
            draft = _generate_via_claude_code(message, profile_md, past_posts)

        if draft:
            _gen_step("done", "Post ready for review!")
            update_draft(draft["id"], {"references": list(_generate_steps)})
            _generate_result = {
                "status": "done",
                "draft_id": draft["id"],
                "title": draft["article"].get("title", ""),
                "generated_by": draft.get("generated_by", engine),
                "post_type": draft.get("post_type", "news"),
                "preview": draft["post_content"][:200],
            }
        else:
            _gen_step("error", "Generation failed")
            _generate_result = {"status": "error", "error": "Could not generate post"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        _gen_step("error", f"Error: {str(e)[:80]}")
        _generate_result = {"status": "error", "error": str(e)}
    finally:
        with _generate_lock:
            _generate_running = False


_GMAIL_KEYWORDS = (
    "gmail", "inbox", "my mail", "my email", "my emails", "newsletter",
    "newsletters", "from email", "from my email", "from mail", "from my mail",
)


def _message_wants_gmail(message: str) -> bool:
    """Heuristic: does the user's prompt actually invite Gmail content?"""
    if not message:
        return False
    m = message.lower()
    return any(kw in m for kw in _GMAIL_KEYWORDS)


def _emit_tool_step(block: dict):
    """Map a stream-json tool_use block to a human-readable progress step."""
    name = block.get("name") or ""
    inp = block.get("input") or {}

    def _basename(p: str) -> str:
        if not p:
            return ""
        return p.replace("\\", "/").rsplit("/", 1)[-1]

    if name == "Read":
        _gen_step("article", f"Reading {_basename(inp.get('file_path', '')) or 'file'}")
    elif name == "Write":
        _gen_step("article", f"Writing {_basename(inp.get('file_path', '')) or 'file'}")
    elif name == "Edit":
        _gen_step("article", f"Editing {_basename(inp.get('file_path', '')) or 'file'}")
    elif name in ("Glob", "Grep"):
        pat = inp.get("pattern") or inp.get("query") or ""
        _gen_step("search", f"{name}: {pat[:60]}")
    elif name == "WebFetch":
        url = inp.get("url") or ""
        _gen_step("search", f"Fetching {url[:60]}")
    elif name.startswith("mcp__claude_ai_Gmail__"):
        op = name.split("__")[-1]
        if op == "search_threads":
            q = inp.get("q") or inp.get("query") or ""
            _gen_step("gmail", f"Gmail search: {q[:60] or '(recent)'}")
        elif op == "get_thread":
            tid = inp.get("thread_id") or inp.get("id") or ""
            _gen_step("gmail", f"Reading Gmail thread {str(tid)[:14]}")
        elif op == "list_threads":
            _gen_step("gmail", "Listing recent Gmail threads")
        elif op == "list_labels":
            _gen_step("gmail", "Browsing Gmail labels")
        else:
            _gen_step("gmail", f"Gmail: {op}")
    elif name.startswith("mcp__"):
        # Generic MCP — strip the prefix for readability
        pretty = name.replace("mcp__", "").replace("__", " ")
        _gen_step("search", f"MCP: {pretty[:60]}")
    elif name == "Bash":
        cmd = (inp.get("command") or "")[:60]
        _gen_step("search", f"Shell: {cmd}")
    elif name:
        _gen_step("search", f"Using {name}")


def _generate_via_claude_code(message: str, profile_md: str, past_posts: str) -> dict:
    """Generate post via Claude Code CLI subprocess, streaming live tool events."""
    import subprocess, shutil

    claude_path = shutil.which("claude")
    if not claude_path:
        _gen_step("error", "Claude CLI not found on PATH")
        return None

    draft_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = POSTS_DIR / f"draft_{draft_id}.json"

    # Only invite Gmail when the user's message actually asks for it.
    gmail_method = get_setting("gmail_method", "none")
    wants_gmail = gmail_method == "claude_mcp" and _message_wants_gmail(message)

    gmail_block = ""
    refs_instruction = ""
    if wants_gmail:
        gmail_block = (
            "\n## Gmail (you have inbox access via MCP)\n"
            "The user is asking you to ground this post in their email content. "
            "Use the Gmail MCP tools to search relevant threads from the last 14 days, "
            "read the most relevant, and ground the post in real content. "
            "Just do it — don't ask for permission.\n"
        )
        refs_instruction = (
            "\nAfter the post text, output a blank line, then the literal marker "
            "`---REFS---` on its own line, then one line per Gmail message you "
            "actually consulted, formatted exactly:\n"
            "  gmail: <subject> | <sender> | <YYYY-MM-DD>\n"
            "Omit the marker entirely if you didn't consult any email. "
            "Do not invent sources.\n"
        )

    prompt = f"""You are ghostwriting a LinkedIn post for a real person. Here is their request:

"{message}"

Write the post based on what they asked for. Infer the post type (news analysis, builder update, hot take, etc.) and desired length from their message.

## Voice profile
{profile_md[:2000]}

## Past posts (match this voice)
{past_posts[:3000]}
{gmail_block}
## Rules
- Sound human. If it reads like AI wrote it, you failed.
- Short paragraphs. Fragments are fine.
- Have a real opinion. Be specific.
- No banned LinkedIn-bro phrases: "In today's fast-paced world", "game-changing", "deep dive", "Here's why this matters", "Thoughts?", "Let that sink in"
- No banned words: delve, leverage, groundbreaking, paradigm, seamless, holistic, transformative, navigate
- No em-dashes as separators
- Max 1-2 emojis. Often zero.
- 3-5 hashtags at the end
- Mobile-first formatting: blank lines between thoughts

Output ONLY the post text. No preamble, no explanation, no JSON wrapping. Just the LinkedIn post content exactly as it would appear.{refs_instruction}"""

    _gen_step("claude", "Claude is thinking…")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # stream-json + --verbose emits one JSON event per line as Claude works,
    # so we can surface real tool calls as live progress steps.
    cli_args = [claude_path, "-p", "--output-format", "stream-json", "--verbose"]
    allowed = ["Read", "Write", "Edit", "WebFetch", "Glob", "Grep"]
    if wants_gmail:
        allowed += [
            "mcp__claude_ai_Gmail",
            "mcp__claude_ai_Gmail__search_threads",
            "mcp__claude_ai_Gmail__get_thread",
            "mcp__claude_ai_Gmail__list_threads",
            "mcp__claude_ai_Gmail__list_labels",
        ]
    cli_args += ["--allowed-tools", ",".join(allowed)]

    final_text = None
    text_chunks: list[str] = []
    try:
        proc = subprocess.Popen(
            cli_args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(BASE_DIR), encoding="utf-8", errors="replace",
            env=env, bufsize=1,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()

        start = time.time()
        for raw_line in proc.stdout:
            if time.time() - start > 240:
                proc.kill()
                _gen_step("error", "Claude timed out after 4 minutes")
                return None
            line = (raw_line or "").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Not JSON — likely raw text fragment, keep as fallback content
                text_chunks.append(line)
                continue

            etype = event.get("type")
            if etype == "assistant":
                msg = event.get("message") or {}
                for block in (msg.get("content") or []):
                    btype = block.get("type")
                    if btype == "tool_use":
                        _emit_tool_step(block)
                    elif btype == "text":
                        txt = block.get("text") or ""
                        if txt:
                            text_chunks.append(txt)
            elif etype == "result":
                # Authoritative final text
                r = event.get("result")
                if isinstance(r, str) and r.strip():
                    final_text = r
            # Other event types (system/init, user/tool_result) ignored —
            # tool results are large and would just clutter progress.

        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _gen_step("error", "Claude CLI timed out")
        return None
    except Exception as e:
        _gen_step("error", f"CLI error: {str(e)[:80]}")
        return None

    raw_out = final_text if final_text is not None else "\n".join(text_chunks)
    raw_out = _fix_double_encoded_utf8((raw_out or "").strip())
    post_text, gmail_refs = _split_post_and_refs(raw_out)
    for ref in gmail_refs:
        _gen_step("gmail", f"From Gmail: {ref}")
    if not post_text or len(post_text) < 50:
        _gen_step("error", f"Output too short ({len(post_text)} chars)")
        return None

    # Build draft JSON
    draft = {
        "id": draft_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending_approval",
        "article": {
            "title": message[:120],
            "summary": message,
            "link": "",
            "source": "Chat prompt",
            "type": "custom",
        },
        "post_content": post_text,
        "post_type": "news",
        "style_used": "A",
        "needs_image": False,
        "image_query": "",
        "image_urls": None,
        "image_path": None,
        "generated_by": "claude_code_cli",
        "profile_used": True,
        "hashtags": _extract_hashtags(post_text),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)

    return draft


def _generate_via_anthropic_api(message: str, profile_md: str, past_posts: str) -> dict:
    """Generate post via the Anthropic API (no CLI needed)."""
    api_key = get_setting("anthropic_api_key", "")
    if not api_key:
        _gen_step("error", "No Anthropic API key configured — set it in Settings > General")
        return None

    model = get_setting("anthropic_model", "claude-sonnet-4-20250514")
    _gen_step("claude", f"Calling Anthropic API ({model.split('-')[1] if '-' in model else model})...")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        system_prompt = f"""You are ghostwriting a LinkedIn post for a real person. Match their voice exactly.

## Voice profile
{profile_md[:2000]}

## Past posts (match this voice)
{past_posts[:3000]}

## Rules
- Sound human. If it reads like AI wrote it, you failed.
- Short paragraphs. Fragments are fine.
- Have a real opinion. Be specific.
- No banned LinkedIn-bro phrases: "In today's fast-paced world", "game-changing", "deep dive", "Here's why this matters", "Thoughts?", "Let that sink in"
- No banned words: delve, leverage, groundbreaking, paradigm, seamless, holistic, transformative, navigate
- No em-dashes as separators
- Max 1-2 emojis. Often zero.
- 3-5 hashtags at the end
- Mobile-first formatting: blank lines between thoughts

Output ONLY the post text. No preamble, no explanation. Just the LinkedIn post content exactly as it would appear."""

        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )
        post_text = resp.content[0].text.strip()

        if not post_text or len(post_text) < 50:
            _gen_step("error", "API returned too little text")
            return None

    except Exception as e:
        _gen_step("error", f"API error: {str(e)[:80]}")
        return None

    draft_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = POSTS_DIR / f"draft_{draft_id}.json"

    draft = {
        "id": draft_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending_approval",
        "article": {
            "title": message[:120],
            "summary": message,
            "link": "",
            "source": "Chat prompt",
            "type": "custom",
        },
        "post_content": post_text,
        "post_type": "news",
        "style_used": "A",
        "needs_image": False,
        "image_query": "",
        "image_urls": None,
        "image_path": None,
        "generated_by": f"anthropic_api ({model})",
        "profile_used": True,
        "hashtags": _extract_hashtags(post_text),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)

    return draft


def _extract_hashtags(text: str) -> list:
    """Pull hashtags from post text."""
    import re
    return re.findall(r"#\w+", text)[:5]


# ─── Regenerate / Rewrite Post ─────────────────────────────────────────────

@app.route("/api/regenerate", methods=["POST"])
def api_regenerate():
    """Regenerate a draft with a mode: shorter, longer, regenerate.
    Body: { id, mode: "shorter"|"longer"|"regenerate" }"""
    global _generate_running, _generate_result, _generate_steps
    data = request.get_json()
    draft_id = data.get("id")
    mode = data.get("mode", "regenerate")

    if not _DRAFT_ID_RE.fullmatch(draft_id or ""):
        return jsonify({"status": "error", "message": "invalid draft_id"}), 400

    # Load existing draft
    draft_file = POSTS_DIR / f"draft_{draft_id}.json"
    if not draft_file.exists():
        return jsonify({"status": "error", "message": "Draft not found"}), 404

    with open(draft_file, "r", encoding="utf-8") as f:
        draft = json.load(f)

    with _generate_lock:
        if _generate_running:
            return jsonify({"status": "error", "message": "Another generation is already running"}), 409
        _generate_running = True
        _generate_result = None
        _generate_steps.clear()

    thread = threading.Thread(
        target=_run_regenerate,
        args=(draft, mode),
        daemon=True
    )
    thread.start()
    return jsonify({
        "status": "ok",
        "message": f"Regenerating post ({mode})..."
    })


def _run_regenerate(draft: dict, mode: str):
    """Background: rewrite an existing post (shorter/longer) or regenerate from same topic."""
    global _generate_running, _generate_result
    try:
        import post_generator
        post_generator._progress_callback = _gen_step

        if mode == "regenerate":
            # Full regeneration from same article/topic
            _gen_step("topic", f"Re-generating: {draft['article'].get('title', '')[:60]}")
            article = draft.get("article", {})
            custom = article.get("type") == "custom"
            post_type = draft.get("post_type", "news")

            from post_generator import generate_post_draft, load_profile
            _gen_step("profile", "Loading your voice profile...")
            profile = load_profile()

            if custom:
                _gen_step("claude", "Claude is writing a fresh take...")
            else:
                _gen_step("article", f"Re-reading article from {article.get('source', '?')}...")

            new_draft = generate_post_draft(article, profile, post_type=post_type)
            if new_draft:
                _gen_step("done", "New version ready!")
                # Supersede the original draft so it doesn't linger as a
                # duplicate pending entry next to the freshly generated one.
                if new_draft["id"] != draft["id"]:
                    moved = _archive_draft_images(draft)
                    sup = {
                        "status": "rejected",
                        "rejected_at": datetime.now().isoformat(),
                        "rejection_reason": "Superseded by regeneration",
                        "superseded_by": new_draft["id"],
                    }
                    if moved:
                        sup["image_paths"] = []
                        sup["image_path"] = None
                    update_draft(draft["id"], sup)
                update_draft(new_draft["id"], {"references": list(_generate_steps)})
                _generate_result = {
                    "status": "done",
                    "draft_id": new_draft["id"],
                    "title": new_draft["article"].get("title", ""),
                    "generated_by": new_draft.get("generated_by", "template"),
                    "post_type": new_draft.get("post_type", "news"),
                    "preview": new_draft["post_content"][:200],
                }
            else:
                _gen_step("error", "Regeneration failed")
                _generate_result = {"status": "error", "error": "Could not regenerate post"}
        else:
            # Shorter or longer rewrite
            _gen_step("claude", f"Rewriting post ({mode})...")
            original = draft.get("post_content", "")

            if mode == "shorter":
                instruction = (
                    "Rewrite the following LinkedIn post to be SHORTER and more punchy. "
                    "Cut it to roughly half the length. Keep the core opinion, remove filler "
                    "and unnecessary examples. Keep it human and opinionated. "
                    "Do NOT add any preamble — output ONLY the rewritten post text."
                )
            else:  # longer
                instruction = (
                    "Rewrite the following LinkedIn post to be LONGER and more detailed. "
                    "Add concrete examples, personal experience references, specific technical "
                    "details. Expand on the argument with a deeper take. Keep the same voice. "
                    "Keep it human — no corporate speak or filler paragraphs. "
                    "Do NOT add any preamble — output ONLY the rewritten post text."
                )

            prompt = f"{instruction}\n\n---\nORIGINAL POST:\n{original}\n---"
            engine = get_setting("gen_engine", "claude_code")

            if engine == "anthropic_api":
                api_key = get_setting("anthropic_api_key", "")
                model = get_setting("anthropic_model", "claude-sonnet-4-20250514")
                if not api_key:
                    _gen_step("error", "No Anthropic API key configured")
                    _generate_result = {"status": "error", "error": "Set your API key in Settings"}
                    return
                _gen_step("claude", f"Calling Anthropic API ({model.split('-')[1] if '-' in model else model})...")
                try:
                    import anthropic
                    client = anthropic.Anthropic(api_key=api_key)
                    resp = client.messages.create(
                        model=model,
                        max_tokens=2000,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    rewritten = resp.content[0].text.strip()
                except Exception as e:
                    _gen_step("error", f"API error: {str(e)[:80]}")
                    _generate_result = {"status": "error", "error": str(e)}
                    return
            else:
                import subprocess, shutil
                claude_path = shutil.which("claude")
                if not claude_path:
                    _gen_step("error", "Claude CLI not found")
                    _generate_result = {"status": "error", "error": "Claude CLI not found on PATH"}
                    return
                # Carry the Gmail intent over from the original draft if its
                # source mentioned email/inbox — otherwise rewrites stay local.
                orig_intent = (draft.get("article", {}).get("summary") or "") + " " + (draft.get("article", {}).get("title") or "")
                rewrite_gmail = get_setting("gmail_method", "none") == "claude_mcp" and _message_wants_gmail(orig_intent)
                cli_args = [claude_path, "-p", "--output-format", "stream-json", "--verbose"]
                allowed = ["Read", "Write", "Edit", "WebFetch", "Glob", "Grep"]
                if rewrite_gmail:
                    allowed += [
                        "mcp__claude_ai_Gmail",
                        "mcp__claude_ai_Gmail__search_threads",
                        "mcp__claude_ai_Gmail__get_thread",
                        "mcp__claude_ai_Gmail__list_threads",
                        "mcp__claude_ai_Gmail__list_labels",
                    ]
                cli_args += ["--allowed-tools", ",".join(allowed)]
                _gen_step("claude", "Claude is rewriting…")
                env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

                final_text_rw = None
                text_chunks_rw: list[str] = []
                try:
                    proc = subprocess.Popen(
                        cli_args,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, cwd=str(POSTS_DIR.parent), encoding="utf-8", errors="replace",
                        env=env, bufsize=1,
                    )
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                    rw_start = time.time()
                    for raw_line in proc.stdout:
                        if time.time() - rw_start > 180:
                            proc.kill()
                            _gen_step("error", "Rewrite timed out")
                            _generate_result = {"status": "error", "error": "Rewrite timed out"}
                            return
                        line = (raw_line or "").strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            text_chunks_rw.append(line)
                            continue
                        et = ev.get("type")
                        if et == "assistant":
                            for blk in (ev.get("message", {}) or {}).get("content", []) or []:
                                bt = blk.get("type")
                                if bt == "tool_use":
                                    _emit_tool_step(blk)
                                elif bt == "text":
                                    t = blk.get("text") or ""
                                    if t:
                                        text_chunks_rw.append(t)
                        elif et == "result":
                            r = ev.get("result")
                            if isinstance(r, str) and r.strip():
                                final_text_rw = r
                    proc.wait(timeout=10)
                except Exception as e:
                    _gen_step("error", f"CLI error: {str(e)[:80]}")
                    _generate_result = {"status": "error", "error": str(e)}
                    return

                rewritten_raw = _fix_double_encoded_utf8(((final_text_rw if final_text_rw is not None else "\n".join(text_chunks_rw)) or "").strip())
                rewritten, _gmail_refs = _split_post_and_refs(rewritten_raw)
                for ref in _gmail_refs:
                    _gen_step("gmail", f"From Gmail: {ref}")

            if not rewritten or len(rewritten) < 50:
                _gen_step("error", "Rewrite produced too little text")
                _generate_result = {"status": "error", "error": "Rewrite failed — output too short"}
                return

            # Save the rewrite as the same draft (overwrite content)
            update_draft(draft["id"], {
                "post_content": rewritten,
                "references": list(_generate_steps),
                "rewrite_history": draft.get("rewrite_history", []) + [{
                    "mode": mode,
                    "ts": datetime.now().isoformat(),
                    "original_length": len(original),
                    "new_length": len(rewritten),
                }]
            })
            _gen_step("done", f"Post rewritten ({mode})!")
            _generate_result = {
                "status": "done",
                "draft_id": draft["id"],
                "title": draft["article"].get("title", ""),
                "generated_by": f"claude_rewrite ({engine})",
                "post_type": draft.get("post_type", "news"),
                "preview": rewritten[:200],
                "mode": mode,
            }
    except Exception as e:
        import traceback
        traceback.print_exc()
        _gen_step("error", f"Error: {str(e)[:80]}")
        _generate_result = {"status": "error", "error": str(e)}
    finally:
        with _generate_lock:
            _generate_running = False


# ─── Builder Updates (GitHub commits) ────────────────────────────────────────

@app.route("/api/builder/commits")
def api_builder_commits():
    """List recent commits from the user's GitHub repos for builder post selection."""
    try:
        from builder_posts import fetch_recent_commits
        force = request.args.get("refresh") == "1"
        commits = fetch_recent_commits(days=14, force_refresh=force)
        return jsonify({"commits": commits[:30]})
    except Exception as e:
        return jsonify({"commits": [], "error": str(e)}), 500


# ─── Settings API ────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    """Return current settings (merged with defaults)."""
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
        except Exception:
            pass
    return jsonify(settings)


@app.route("/api/settings", methods=["PUT", "POST"])
def api_settings_save():
    """Update settings. Body: { key: value, ... }"""
    data = request.get_json(silent=True) or {}
    current = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                current.update(json.load(f))
        except Exception:
            pass
    # Only accept keys that are part of the known settings schema, and clamp
    # integer fields to a sane minimum so a bad value (e.g. a zero reminder
    # interval) can't break the scheduler or trigger a busy-loop.
    INT_FIELDS = {"reminder_interval_minutes", "max_cards_per_day"}
    clean = {}
    for key, val in data.items():
        if key not in DEFAULT_SETTINGS:
            continue  # ignore unknown keys
        if key in INT_FIELDS:
            try:
                val = max(1, int(val))
            except (TypeError, ValueError):
                continue  # skip non-numeric values for integer fields
        clean[key] = val
    current.update(clean)
    save_settings(current)
    return jsonify({"status": "ok", "settings": current})


@app.route("/api/past-posts", methods=["GET"])
def api_past_posts_get():
    """Return the contents of my_past_posts.md for the voice editor."""
    past_posts_file = BASE_DIR / "my_past_posts.md"
    if past_posts_file.exists():
        return jsonify({"content": past_posts_file.read_text(encoding="utf-8")})
    return jsonify({"content": ""})


@app.route("/api/past-posts", methods=["PUT", "POST"])
def api_past_posts_save():
    """Save updates to my_past_posts.md."""
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    past_posts_file = BASE_DIR / "my_past_posts.md"
    past_posts_file.write_text(content, encoding="utf-8")
    return jsonify({"status": "ok"})


# ─── Profile + first-run setup ───────────────────────────────────────────────

@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    """Return profile.md content (for the profile editor)."""
    if PROFILE_FILE.exists():
        return jsonify({"content": PROFILE_FILE.read_text(encoding="utf-8")})
    return jsonify({"content": ""})


@app.route("/api/profile", methods=["PUT", "POST"])
def api_profile_save():
    """Write profile.md verbatim (raw markdown editor)."""
    data = request.get_json(silent=True) or {}
    PROFILE_FILE.write_text(data.get("content", ""), encoding="utf-8")
    return jsonify({"status": "ok"})


@app.route("/api/setup-status", methods=["GET"])
def api_setup_status():
    """Tell the UI whether first-run setup is still needed."""
    completed = bool(get_setting("setup_completed", False))
    return jsonify({
        "completed": completed,
        "has_profile": PROFILE_FILE.exists(),
        "has_past_posts": (BASE_DIR / "my_past_posts.md").exists(),
    })


def _build_profile_md(d: dict) -> str:
    """Render a profile.md from the wizard's collected fields."""
    def g(k, default=""):
        # Collapse newlines/carriage returns to spaces so a wizard field can't
        # inject extra markdown lines or fake instructions into profile.md
        # (which is embedded verbatim in the Claude generation prompt).
        return " ".join((d.get(k) or default).split())

    interests = d.get("interests") or []
    interest_lines = "\n".join(
        f"- [x] {' '.join(str(i).split())}" for i in interests
    ) or "- [x] AI agents & automation"
    return f"""# LinkedIn Profile & Posting Strategy

## Who Am I
- Name: {g('name')}
- Title/Role: {g('role')}
- Industry: {g('industry') or g('topic')}
- Posts about: {g('topic') or g('industry') or 'my industry'}
- Key skills: {g('skills')}
- Current company/project: {g('company')}
- Location: {g('location')}
- LinkedIn: {g('linkedin')}
- GitHub: {('https://github.com/' + g('github')) if g('github') else ''}
- Status: {g('status')}

## My Interests
{interest_lines}

## My Audience
- Target audience: {g('audience')}
- Tone: {g('tone', 'casual-professional — knowledgeable but approachable')}

## Posting Style Preferences
- Post length preference: {g('length', 'mix of short and medium')}
- Use emojis: {g('emojis', 'minimal (1-2 max)')}
- Use hashtags: 3-5 max per post
- Personal opinion in posts: yes
- Include call-to-action: soft questions

## Post Content Strategy
- Type A (40%): industry/news analysis with your perspective
- Type B (40%): builder updates — what you're shipping
- Type C (20%): quick takes / hot opinions

## Writing Voice
{g('voice', '- Direct and to the point\\n- Simple words, no corporate jargon\\n- Honest and authentic')}

## Topics to AVOID
- Overly corporate / LinkedIn-bro language
{('- ' + g('avoid')) if g('avoid') else '- Fake motivation posts'}
"""


@app.route("/api/setup", methods=["POST"])
def api_setup():
    """First-run setup: write profile.md from the wizard fields, seed
    my_past_posts.md from the template if missing, and persist key settings."""
    data = request.get_json(silent=True) or {}

    # 1. Write profile.md from the collected fields
    try:
        PROFILE_FILE.write_text(_build_profile_md(data), encoding="utf-8")
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not write profile: {e}"}), 500

    # 2. Seed my_past_posts.md from the example template if it doesn't exist yet
    past_posts_file = BASE_DIR / "my_past_posts.md"
    example_pp = BASE_DIR / "examples" / "my_past_posts.example.md"
    if not past_posts_file.exists() and example_pp.exists():
        try:
            past_posts_file.write_text(example_pp.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    # 3. Persist settings (github username, gmail method, engine) + mark done
    current = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                current.update(json.load(f))
        except Exception:
            pass
    if data.get("github"):
        current["github_username"] = data["github"].strip()
    if data.get("topic"):
        current["content_topic"] = data["topic"].strip()
    if data.get("gmail_method"):
        current["gmail_method"] = data["gmail_method"]
    if data.get("gen_engine"):
        current["gen_engine"] = data["gen_engine"]
    current["setup_completed"] = True
    save_settings(current)

    return jsonify({"status": "ok", "message": "Setup complete!"})


@app.route("/api/setup/skip", methods=["POST"])
def api_setup_skip():
    """Mark setup as completed without filling the wizard (user dismissed it)."""
    current = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                current.update(json.load(f))
        except Exception:
            pass
    current["setup_completed"] = True
    save_settings(current)
    return jsonify({"status": "ok"})


# ─── Notification System ────────────────────────────────────────────────────

def send_reminder_notification():
    try:
        from plyer import notification
        pending = get_pending_posts()
        if pending:
            notification.notify(
                title="LinkedIn Post Awaiting Approval",
                message=f"You have {len(pending)} post(s) ready to review. Open http://127.0.0.1:{APPROVAL_PORT}",
                app_name="Linkedin-A01",
                timeout=10,
            )
    except Exception as e:
        print(f"[Notification Error] {e}")


def reminder_loop():
    while True:
        pending = get_pending_posts()
        if pending:
            send_reminder_notification()
        # Read the interval live each iteration so a Settings change takes
        # effect without a server restart. Clamp to a minimum of 60s so a
        # misconfigured interval of 0 cannot spin into an unbounded busy-loop
        # hammering the database.
        try:
            interval_min = int(get_setting("reminder_interval_minutes", REMINDER_INTERVAL_MINUTES))
        except (TypeError, ValueError):
            interval_min = REMINDER_INTERVAL_MINUTES
        time.sleep(max(60, interval_min * 60))


# ─── Server Entry ────────────────────────────────────────────────────────────

def run_server(open_browser=True):
    """Start the dashboard server."""
    print(f"[Dashboard] Starting at http://{APPROVAL_HOST}:{APPROVAL_PORT}")

    reminder_thread = threading.Thread(target=reminder_loop, daemon=True)
    reminder_thread.start()

    if open_browser:
        webbrowser.open(f"http://{APPROVAL_HOST}:{APPROVAL_PORT}")

    app.run(host=APPROVAL_HOST, port=APPROVAL_PORT, debug=False)


if __name__ == "__main__":
    run_server()
