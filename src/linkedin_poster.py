"""Posts approved content to LinkedIn using Playwright."""
import json
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

from config import AUTH_DIR, POSTS_DIR


async def login_and_save_session():
    """Run once to log in manually and save the session."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login")

        print("\n" + "=" * 50)
        print("  LOG IN TO LINKEDIN")
        print("=" * 50)
        print("\n  A browser window has opened.")
        print("  1. Log in with your LinkedIn credentials")
        print("  2. Complete any 2FA if prompted")
        print("  3. Wait until you see your feed")
        print("  4. Come back here and press Enter")
        print("\n" + "=" * 50)
        input("\n  Press Enter after you're logged in... ")

        AUTH_DIR.mkdir(exist_ok=True)
        await context.storage_state(path=str(AUTH_DIR / "linkedin.json"))
        print("\n  Session saved successfully!")
        print("  You won't need to log in again unless the session expires.\n")
        await browser.close()


async def post_to_linkedin(content: str, image_path=None) -> bool:
    """Post content (and optional image(s)) to LinkedIn.
    image_path may be a single path (str) or a list of paths for a carousel."""
    # Normalize to a list of existing files
    if image_path is None:
        image_paths = []
    elif isinstance(image_path, (list, tuple)):
        image_paths = [str(p) for p in image_path if p]
    else:
        image_paths = [str(image_path)]
    image_paths = [p for p in image_paths if Path(p).exists()]
    _images_dir = (POSTS_DIR / "images").resolve()
    image_paths = [p for p in image_paths if str(Path(p).resolve()).startswith(str(_images_dir))]

    auth_file = AUTH_DIR / "linkedin.json"
    if not auth_file.exists():
        print("[Poster] No saved session. Run: python linkedin_poster.py --login")
        return False, {"error": "No saved LinkedIn session — run python linkedin_poster.py --login", "screenshot": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            print("[Poster] Opening LinkedIn feed...")
            await page.goto("https://www.linkedin.com/feed/",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)

            # Check if we're still logged in (redirected to login or checkpoint)
            if "/login" in page.url or "/checkpoint" in page.url or "/uas/login" in page.url:
                print(f"[Poster] Session expired or checkpoint required. URL: {page.url}")
                print("[Poster] Run: python linkedin_poster.py --login")
                await browser.close()
                return False, {"error": f"Session expired (redirected to {page.url}). Re-login required.", "screenshot": None}

            # Click the "Start a post" trigger — in 2026 LinkedIn this is a DIV
            # with aria-label. Supports both English ("Start a post") and Arabic ("بدء منشور")
            print("[Poster] Opening post dialog...")
            trigger_selectors = [
                "[aria-label='بدء منشور']",          # Arabic
                "[aria-label='Start a post']",         # English
                "[aria-label*='Start a post']",
                "[aria-label*='بدء']",
                "[aria-label*='Create a post']",
                "button.share-box-feed-entry__trigger",  # legacy fallback
            ]
            clicked = False
            for sel in trigger_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        print(f"[Poster] Clicked trigger via: {sel}")
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                raise Exception("Could not find 'Start a post' trigger")
            await page.wait_for_timeout(3500)  # wait for modal to render

            # Wait for the modal to fully render the editor (lazy-loaded)
            await page.wait_for_timeout(2000)

            # Type in the post editor — scope queries to the dialog
            print("[Poster] Writing post content...")
            editor_selectors = [
                "[role='dialog'] [contenteditable='true']",
                "[role='dialog'] div.ql-editor",
                "[role='dialog'] [role='textbox']",
                "div.ql-editor[contenteditable='true']",
                "[contenteditable='true'][data-placeholder]",
                "[contenteditable='true'][role='textbox']",
                "div[role='textbox']",
                "[contenteditable='true']",
            ]
            editor = None
            used_sel = None
            for sel in editor_selectors:
                try:
                    e = page.locator(sel).first
                    if await e.is_visible(timeout=3500):
                        editor = e
                        used_sel = sel
                        break
                except Exception:
                    continue
            if editor is None:
                raise Exception("Could not find post editor")
            print(f"[Poster] Editor found via: {used_sel}")
            await editor.click()
            await page.wait_for_timeout(500)
            # keyboard.insert_text is FAST and preserves newlines in contenteditables
            # (much better than type() for long posts — no per-char keystroke events)
            await page.keyboard.insert_text(content)
            await page.wait_for_timeout(1500)

            # Upload image if provided.
            # LinkedIn (2026) approach: The file input may not exist in the
            # dialog until the media button is clicked. We try multiple strategies:
            # A. Look for ANY file input in/near the dialog (including hidden ones)
            # B. Click the media/photo button to reveal the file picker area, then use file input
            # C. Try global page file inputs as last resort
            if image_paths:
                print(f"[Poster] Uploading {len(image_paths)} image(s): {image_paths}")
                uploaded = False

                # Strategy A: click media button FIRST to reveal the file input
                # LinkedIn 2026 lazy-loads the file input only after media panel opens
                media_selectors = [
                    "[role='dialog'] button[aria-label*='إضافة وسائط' i]",
                    "[role='dialog'] button[aria-label*='Add media' i]",
                    "[role='dialog'] button[aria-label*='media' i]",
                    "[role='dialog'] button[aria-label*='image' i]",
                    "[role='dialog'] button[aria-label*='photo' i]",
                    "[role='dialog'] button[aria-label*='صورة']",
                    "[role='dialog'] button[aria-label*='وسائط']",
                    "[role='dialog'] button[aria-label*='إضافة']",
                    "button[aria-label='Add media']",
                    # Icon-based fallback: toolbar buttons inside dialog footer
                    "[role='dialog'] .share-creation-state__footer button:first-child",
                    "[role='dialog'] [data-test-icon='image-medium']",
                    "[role='dialog'] li.share-creation-state__media button",
                ]
                media_clicked = False
                for sel in media_selectors:
                    try:
                        b = page.locator(sel).first
                        if await b.is_visible(timeout=1500):
                            await b.click()
                            print(f"[Poster] Opened media panel via: {sel}")
                            media_clicked = True
                            await page.wait_for_timeout(2500)
                            break
                    except Exception:
                        continue

                # Now look for file inputs (may be in dialog OR newly revealed panel)
                file_input_selectors = [
                    "[role='dialog'] input[type='file'][accept*='image']",
                    "[role='dialog'] input[type='file']",
                    "input[type='file'][accept*='image']",
                    "input[type='file']",
                ]
                for sel in file_input_selectors:
                    try:
                        file_inputs = page.locator(sel)
                        n = await file_inputs.count()
                        if n > 0:
                            # Prefer the one with accept=image
                            target = file_inputs.first
                            for i in range(n):
                                inp = file_inputs.nth(i)
                                try:
                                    accept = await inp.get_attribute("accept") or ""
                                    if "image" in accept.lower():
                                        target = inp
                                        break
                                except Exception:
                                    pass
                            # Pass the whole list so LinkedIn builds a carousel
                            await target.set_input_files(image_paths)
                            print(f"[Poster] {len(image_paths)} file(s) set via: {sel} (found {n} inputs)")
                            await page.wait_for_timeout(5000)
                            uploaded = True
                            break
                    except Exception as e:
                        print(f"[Poster] file input '{sel}' failed: {e}")
                        continue

                # Strategy B: if no file input found, try drag-drop via page.dispatch_event
                if not uploaded:
                    print("[Poster] No file input found. Trying JS-based file dispatch...")
                    try:
                        # Find a drop zone (the editor or dialog content area)
                        drop_target = page.locator("[role='dialog'] .ql-editor, [role='dialog'] [contenteditable='true'], [role='dialog']").first
                        await drop_target.dispatch_event("drop", {})
                        await page.wait_for_timeout(1000)
                        # Check again for file inputs
                        fi = page.locator("input[type='file']").first
                        if await fi.count() > 0:
                            await fi.set_input_files(image_paths)
                            await page.wait_for_timeout(5000)
                            uploaded = True
                            print("[Poster] File set after dispatch_event drop")
                    except Exception as e:
                        print(f"[Poster] Dispatch drop strategy failed: {e}")

                if uploaded:
                    # Click Done / Next / موافق to confirm the image
                    # Wait a moment for the image preview to render
                    await page.wait_for_timeout(2000)
                    done_selectors = [
                        # Language-agnostic: LinkedIn artdeco primary button (works in all UI languages)
                        "[role='dialog'] button.artdeco-button--primary",
                        # Language-agnostic: aria-label matching (set by LinkedIn regardless of display language)
                        "[role='dialog'] button[aria-label='Done']",
                        "[role='dialog'] button[aria-label='Next']",
                        # Text-based fallbacks for English
                        "[role='dialog'] button:has-text('Done')",
                        "[role='dialog'] button:has-text('Next')",
                        # Text-based fallbacks for Arabic
                        "[role='dialog'] button:has-text('موافق')",
                        "[role='dialog'] button:has-text('التالي')",
                        "[role='dialog'] button:has-text('انتهى')",
                        "[role='dialog'] button:has-text('تم')",
                        # 2026 LinkedIn sometimes uses "Back" to return to post editor
                        "[role='dialog'] button:has-text('Back')",
                        "[role='dialog'] button:has-text('رجوع')",
                    ]
                    for sel in done_selectors:
                        try:
                            b = page.locator(sel).last
                            if await b.is_visible(timeout=2000):
                                await b.click()
                                print(f"[Poster] Confirmed image via: {sel}")
                                break
                        except Exception:
                            continue
                    await page.wait_for_timeout(2000)
                else:
                    print("[Poster] WARNING: could not upload image; posting text only")
                    # Take a diagnostic screenshot so we can debug next time
                    diag_path = POSTS_DIR / f"image_fail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=str(diag_path))
                    print(f"[Poster] Diagnostic screenshot: {diag_path}")

            # Click the Post button — prefer EXACT aria-label/text so we never
            # match the chevron next to it (which opens the schedule dialog).
            print("[Poster] Submitting post...")

            async def _click_publish() -> bool:
                # 1. Most specific: aria-label exactly "نشر" or "Post"
                for sel in (
                    "[role='dialog'] button[aria-label='نشر']",
                    "[role='dialog'] button[aria-label='Post']",
                ):
                    try:
                        b = page.locator(sel).first
                        if await b.is_visible(timeout=2500):
                            await b.click()
                            print(f"[Poster] Clicked submit via: {sel}")
                            return True
                    except Exception:
                        continue
                # 2. Role+name exact match (handles divs-as-buttons and labels)
                for name in ("نشر", "Post"):
                    try:
                        b = page.get_by_role("button", name=name, exact=True).last
                        if await b.is_visible(timeout=2000):
                            await b.click()
                            print(f"[Poster] Clicked submit via role=button name={name}")
                            return True
                    except Exception:
                        continue
                # 3. Class-based legacy fallback — explicitly avoid the schedule chevron
                for sel in (
                    "[role='dialog'] button.share-actions__primary-action:not([aria-label*='جدولة']):not([aria-label*='Schedule'])",
                    "button.share-actions__primary-action:not([aria-label*='جدولة']):not([aria-label*='Schedule'])",
                ):
                    try:
                        b = page.locator(sel).last
                        if await b.is_visible(timeout=2000):
                            await b.click()
                            print(f"[Poster] Clicked submit via fallback: {sel}")
                            return True
                    except Exception:
                        continue
                return False

            async def _dismiss_schedule_if_open() -> bool:
                """If the schedule dialog popped up by mistake, close it and return True."""
                schedule_titles = [
                    "[role='dialog']:has-text('جدولة المنشور')",
                    "[role='dialog']:has-text('Schedule post')",
                ]
                for sel in schedule_titles:
                    try:
                        d = page.locator(sel).first
                        if await d.is_visible(timeout=1200):
                            print("[Poster] Schedule dialog open by mistake — dismissing.")
                            # Try the explicit Back button first, then the close (X)
                            for back in (
                                f"{sel} button:has-text('رجوع')",
                                f"{sel} button:has-text('Back')",
                                f"{sel} button[aria-label='إغلاق']",
                                f"{sel} button[aria-label='Close']",
                                f"{sel} button[aria-label='Dismiss']",
                                f"{sel} [aria-label='إغلاق']",
                                f"{sel} [aria-label='Close']",
                            ):
                                try:
                                    bb = page.locator(back).first
                                    if await bb.is_visible(timeout=800):
                                        await bb.click()
                                        await page.wait_for_timeout(800)
                                        return True
                                except Exception:
                                    continue
                            # Last resort: press Escape
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(800)
                            return True
                    except Exception:
                        continue
                return False

            posted_clicked = await _click_publish()
            # If a scheduler dialog appeared (LinkedIn sometimes does this when
            # the chevron part of the split button gets the click), close it
            # and try once more on the primary publish action.
            if await _dismiss_schedule_if_open():
                posted_clicked = await _click_publish()

            if not posted_clicked:
                raise Exception("Could not find Post submit button")

            # Confirm submission by waiting for the composer dialog to close.
            # If it doesn't, treat as failure — better to surface than silently lie.
            try:
                await page.wait_for_selector(
                    "[role='dialog']:has([contenteditable='true'])",
                    state="detached",
                    timeout=15000,
                )
                print("[Poster] Composer dialog closed — post submitted.")
            except Exception:
                # Last check: schedule dialog may have re-opened. If so, fail loudly.
                if await _dismiss_schedule_if_open():
                    raise Exception("Schedule dialog kept opening — publish click failed.")
                raise Exception("Composer did not close after publish click (likely silent failure).")

            await page.wait_for_timeout(2500)
            print("[Poster] Post submitted successfully!")

            # Save updated session state
            await context.storage_state(path=str(auth_file))
            await browser.close()
            return True, None

        except Exception as e:
            err_msg = str(e) or e.__class__.__name__
            print(f"[Poster] Error: {err_msg}")
            screenshot_path = POSTS_DIR / f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            try:
                await page.screenshot(path=str(screenshot_path))
                print(f"[Poster] Error screenshot saved: {screenshot_path}")
            except Exception:
                screenshot_path = None
            await browser.close()
            return False, {"error": err_msg, "screenshot": str(screenshot_path) if screenshot_path else None}


async def post_approved_drafts():
    """Find approved drafts and post them."""
    posted_count = 0
    failed_count = 0
    for f in sorted(POSTS_DIR.glob("draft_*.json")):
        with open(f, "r", encoding="utf-8") as fp:
            draft = json.load(fp)

        if draft.get("status") != "approved":
            continue

        print(f"\n[Poster] Posting draft: {f.name}")
        content = draft["post_content"]
        # Support both the new image_paths list and the legacy single image_path.
        image_list = draft.get("image_paths")
        if not (isinstance(image_list, list) and image_list):
            single = draft.get("image_path")
            image_list = [single] if single else []

        success, err_info = await post_to_linkedin(content, image_list)

        if success:
            draft["status"] = "posted"
            draft["posted_at"] = datetime.now().isoformat()
            # Clear any stale failure markers on a successful retry
            draft.pop("post_error", None)
            draft.pop("post_error_at", None)
            draft.pop("post_error_screenshot", None)
            posted_count += 1
        else:
            draft["status"] = "post_failed"
            draft["failed_at"] = datetime.now().isoformat()
            if err_info:
                draft["post_error"] = err_info.get("error", "Unknown error")
                draft["post_error_at"] = datetime.now().isoformat()
                if err_info.get("screenshot"):
                    draft["post_error_screenshot"] = err_info["screenshot"]
            failed_count += 1

        with open(f, "w", encoding="utf-8") as fp:
            json.dump(draft, fp, indent=2, ensure_ascii=False)

    if posted_count or failed_count:
        print(f"\n[Poster] Done — posted {posted_count}, failed {failed_count}.")
    else:
        print("\n[Poster] No approved drafts to post.")


if __name__ == "__main__":
    if "--login" in sys.argv:
        asyncio.run(login_and_save_session())
    else:
        asyncio.run(post_approved_drafts())
