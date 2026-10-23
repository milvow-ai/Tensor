"""Google Gemini CLI (`gemini`, npm @google/gemini-cli) agent executor.

One Google login per connection: `meta.home` redirects `USERPROFILE`/`HOME`, so the CLI keeps its
`~/.gemini` settings and OAuth credentials under that directory (same mechanism as the agy driver).
Output parsing and error mapping are shared with the agy driver; only the command line differs.
"""

from typing import Any

from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.classify import classify_gemini


class GeminiCliExecutor(AgyCliExecutor):
    """Executes tasks via the official Gemini CLI in headless mode."""

    default_bin = "gemini"
    classify = staticmethod(classify_gemini)

    def build_argv(
        self,
        cli_bin: str,
        task: str,
        model: Any,
        session_id: Any,
        mode: str,
        json_schema: Any,
    ) -> list[str]:
        argv: list[str] = [cli_bin, "-p", task, "--output-format", "json", "--skip-trust"]
        if model:
            argv.extend(["--model", str(model)])
        if session_id:
            argv.extend(["--resume", str(session_id)])
        if mode == "edit":
            argv.extend(["--approval-mode", "yolo"])
        return argv
