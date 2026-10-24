"""Stands in for the claude CLI in tests: the ``auth login`` transcript of Claude Code 2.1.299 and its stdin code.

The code ``good-code`` signs in (FAKE_CLAUDE_EMAIL, default jane@example.com); anything else fails like a refused code.
"""

import json
import os
import sys
from pathlib import Path

home = Path(os.environ["CLAUDE_CONFIG_DIR"])
args = sys.argv[1:]
email = os.environ.get("FAKE_CLAUDE_EMAIL", "jane@example.com")
if args[:2] == ["auth", "login"]:
    print("Opening browser to sign in…")
    print(
        "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=x"
        "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=s",
        flush=True,
    )
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code != "good-code":
        print("Login failed: Request failed with status code 400")
        sys.exit(1)
    (home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "fake"}}), encoding="utf-8")
    (home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": email, "organizationUuid": "org-1"}}), encoding="utf-8"
    )
    print("Login successful.")
elif args == ["auth", "status", "--json"]:
    if (home / ".credentials.json").exists():
        print(json.dumps({"loggedIn": True, "email": email, "orgId": "org-1", "orgName": "Jane's Org",
                          "subscriptionType": "pro"}))
    else:
        print(json.dumps({"loggedIn": False}))
elif args[:1] == ["-p"]:
    if (home / ".credentials.json").exists():
        print("PONG")
    else:
        print("Invalid API key · Please run /login")
        sys.exit(1)
else:
    sys.exit(2)
