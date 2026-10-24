"""Stands in for the codex CLI in tests: the device-code transcript of codex 0.160, then a login or a timeout.

FAKE_CODEX_MODE: approve (default) | timeout. FAKE_CODEX_DELAY: seconds before the "approval" (default 1).
"""

import base64
import json
import os
import sys
import time
from pathlib import Path


def b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


home = Path(os.environ["CODEX_HOME"])
args = sys.argv[1:]
if args[:2] == ["login", "--device-auth"]:
    print("\nWelcome to Codex [v0.160.0]\nOpenAI's command-line coding agent\n")
    print("Follow these steps to sign in with ChatGPT using device code authorization:\n")
    print("1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n")
    print("2. Enter this one-time code (expires in 15 minutes)\n   F91M-9W7OM\n", flush=True)
    time.sleep(float(os.environ.get("FAKE_CODEX_DELAY", "1")))
    if os.environ.get("FAKE_CODEX_MODE", "approve") == "timeout":
        print("Error logging in with device code: device auth timed out after 15 minutes", file=sys.stderr)
        sys.exit(1)
    claims = {
        "email": os.environ.get("FAKE_CODEX_EMAIL", "jane@example.com"),
        "sub": "auth0|jane",
        "https://api.openai.com/auth": {"chatgpt_user_id": "user-jane", "chatgpt_plan_type": "plus"},
    }
    token = f"{b64({'alg': 'none'})}.{b64(claims)}.sig"
    (home / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": token, "account_id": "acc-1"}}), encoding="utf-8"
    )
    print("Successfully logged in")
elif args[:1] == ["exec"]:
    if (home / "auth.json").exists():
        print("PONG")
    else:
        print("Not logged in")
        sys.exit(1)
else:
    sys.exit(2)
