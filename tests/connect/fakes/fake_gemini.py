"""Scripted Gemini OAuth and ACP exchange. Any inference request fails the test."""

import json
import os
import sys
from pathlib import Path

assert "--acp" in sys.argv and "-p" not in sys.argv and "models" not in sys.argv
assert os.environ["NO_BROWSER"] == "true"
assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
home = Path(os.environ["GEMINI_CLI_HOME"]) / ".gemini"
settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
creds = home / "oauth_creds.json"
if not creds.exists():
    print("https://accounts.google.com/o/oauth2/v2/auth?state=fake-state", flush=True)
    print("Enter the authorization code: ", end="", flush=True)
    if sys.stdin.readline().strip() != "fake-code":
        sys.exit(1)
    creds.write_text(json.dumps({"access_token": "fake-google-token"}), encoding="utf-8")

first = json.loads(sys.stdin.readline())
assert first["method"] == "initialize"
print(json.dumps({"jsonrpc": "2.0", "id": first["id"], "result": {"protocolVersion": 1}}), flush=True)
second = json.loads(sys.stdin.readline())
assert second["method"] == "session/new" and not second["params"]["mcpServers"]
print(json.dumps({
    "jsonrpc": "2.0", "id": second["id"],
    "result": {"models": {"availableModels": [{"modelId": "gemini-example-preview"}]}},
}), flush=True)
assert not sys.stdin.readline(), "Farm attempted a model turn during setup"
