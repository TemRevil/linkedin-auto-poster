"""
One-time Gmail API setup for reading AI newsletters.
This is OPTIONAL - the system works without it (uses RSS + web scraping instead).

To set up Gmail access:
1. Go to https://console.cloud.google.com/
2. Create a project (or use existing)
3. Enable Gmail API
4. Create OAuth 2.0 credentials (Desktop app)
5. Download credentials.json and place in this folder
6. Run this script to authenticate
"""
import os
import stat
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # project root (src/ is one level down)
CREDS_FILE = _ROOT / "credentials.json"
TOKEN_FILE = _ROOT / "auth_state" / "gmail_token.json"


def setup_gmail():
    """Authenticate with Gmail API and save token."""
    if not CREDS_FILE.exists():
        print("\n" + "=" * 50)
        print("  GMAIL SETUP")
        print("=" * 50)
        print("""
  To connect your Gmail for AI newsletter scraping:

  1. Go to: https://console.cloud.google.com/
  2. Create a new project (e.g., "LinkedIn AutoPoster")
  3. Enable the Gmail API:
     - APIs & Services -> Library -> Search "Gmail API" -> Enable
  4. Create credentials:
     - APIs & Services -> Credentials -> Create -> OAuth 2.0 Client ID
     - Application type: Desktop app
     - Download the JSON file
  5. Rename it to 'credentials.json' and put it in:
     {folder}
  6. Run this script again.

  NOTE: This is optional! The system works fine without Gmail
  by using RSS feeds and web scraping instead.
""".format(folder=CREDS_FILE.parent))
        return

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

        creds = None
        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
                creds = flow.run_local_server(port=0)

            TOKEN_FILE.parent.mkdir(exist_ok=True)
            TOKEN_FILE.write_text(creds.to_json())
            if os.name == "posix":
                os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

        print("\n  Gmail authenticated successfully!")
        print(f"  Token saved to: {TOKEN_FILE}")
        print("  Your AI newsletters will now be included in news scraping.\n")

    except ImportError:
        print("  Install required packages: pip install google-auth-oauthlib google-api-python-client")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    setup_gmail()
