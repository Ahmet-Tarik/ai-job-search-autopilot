#!/usr/bin/env python3
"""One-time Gmail authorization with the READ-ONLY scope (OAuth 2.0, installed app + PKCE).

Nothing secret ever passes through Claude or the chat:
  - you download your own OAuth client file from Google Cloud to
    ~/.config/job-search/gmail_client_secret.json (see autopilot/README.md);
  - this script opens Google's consent page in your browser; you sign in there;
  - Google redirects to a local 127.0.0.1 port; the refresh token is stored in
    ~/.config/job-search/gmail_token.json (chmod 600) and never printed.

Scope: https://www.googleapis.com/auth/gmail.readonly - Google itself refuses any
send, delete, label or settings change made with this token.

Usage: python3 autopilot/gmail_auth.py      (re-run any time to re-authorize)
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

CONF = Path(os.path.expanduser("~/.config/job-search"))
CLIENT = CONF / "gmail_client_secret.json"
TOKEN = CONF / "gmail_token.json"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def main() -> int:
    if not CLIENT.exists():
        print(f"Missing {CLIENT}. Download your OAuth client (Desktop app) JSON there first.")
        return 1
    raw = json.loads(CLIENT.read_text())
    c = raw.get("installed") or raw.get("web") or {}
    cid, csecret = c.get("client_id"), c.get("client_secret")
    if not cid:
        print("The client file has no client_id. Create an OAuth client of type 'Desktop app'.")
        return 1

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    redirect = f"http://127.0.0.1:{port}"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                result.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Gmail read-only access granted. You can close this tab.".encode())

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Google's consent page in your browser (read-only Gmail access)...")
    webbrowser.open(url)
    t.join(timeout=300)
    srv.server_close()
    if "code" not in result:
        print(f"No authorization received ({result.get('error', 'timeout')}).")
        return 1
    body = urllib.parse.urlencode({
        "code": result["code"], "client_id": cid, "client_secret": csecret or "",
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=body, timeout=30) as r:
        tok = json.loads(r.read())
    if SCOPE not in tok.get("scope", "").split():
        print("Google did not grant the read-only scope; nothing saved.")
        return 1
    if "refresh_token" not in tok:
        print("No refresh token returned; remove the app's access at myaccount.google.com and retry.")
        return 1
    TOKEN.write_text(json.dumps({"refresh_token": tok["refresh_token"], "scope": tok["scope"]}))
    os.chmod(TOKEN, 0o600)
    print("Saved read-only Gmail authorization. Test it with: python3 autopilot/gmail_monitor.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
