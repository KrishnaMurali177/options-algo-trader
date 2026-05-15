#!/usr/bin/env python
"""One-time Gmail OAuth2 setup — run locally (not in Docker) to authorize.

Usage:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
    python options_agent/scripts/setup_gmail_auth.py

This opens a browser for Google sign-in. After you click "Allow",
a gmail_token.json is saved in options_agent/config/ and the Docker
containers will use it automatically (no browser needed again).
"""

import sys
from pathlib import Path

config_dir = Path(__file__).resolve().parent.parent / "config"
creds_file = config_dir / "gmail_credentials.json"
token_file = config_dir / "gmail_token.json"

if not creds_file.exists():
    print(f"ERROR: {creds_file} not found.")
    print("Download it from Google Cloud Console → Credentials → OAuth 2.0 Client ID → Download JSON")
    sys.exit(1)

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
creds = flow.run_local_server(port=0)
token_file.write_text(creds.to_json())

print(f"\nSaved token to {token_file}")
print("Gmail alerts are now configured. The Docker containers will use this token automatically.")
