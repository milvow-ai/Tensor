"""Fake CLI test harness for mocking claude, codex, agy, and hermes executables."""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


class FakeCliHarness:
    """Manages temporary fake CLI executables on Windows and POSIX systems."""

    def __init__(self, tmp_dir: Path | None = None) -> None:
        if tmp_dir is None:
            self._temp_dir_obj = tempfile.TemporaryDirectory()
            self.bin_dir = Path(self._temp_dir_obj.name)
        else:
            self._temp_dir_obj = None
            self.bin_dir = tmp_dir

        self.config_file = self.bin_dir / "fake_cli_config.json"
        self.calls_file = self.bin_dir / "fake_cli_calls.json"
        (self.bin_dir / "workdir").mkdir(parents=True, exist_ok=True)

        # Create the fake CLI runner script
        self.runner_script = self.bin_dir / "fake_runner.py"
        self._write_runner_script()

        # Set default empty config and calls
        self.set_response(stdout="")
        self.clear_calls()

    def _write_runner_script(self) -> None:
        code = """
import json
import os
from pathlib import Path
import sys
import time

bin_dir = Path(__file__).parent.resolve()
config_file = bin_dir / "fake_cli_config.json"
calls_file = bin_dir / "fake_cli_calls.json"

# Load config
config = {}
if config_file.exists():
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except Exception:
        pass

# Read stdin if available
stdin_data = ""
try:
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read()
except Exception:
    pass

raw_argv = sys.argv[1:]
argv_for_compat = list(raw_argv)
if stdin_data and stdin_data.strip() and stdin_data.strip() not in argv_for_compat:
    argv_for_compat.append(stdin_data.strip())

# Record call
call_info = {
    "argv": argv_for_compat,
    "raw_argv": raw_argv,
    "stdin": stdin_data,
    "cwd": os.getcwd(),
    "env": {
        "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR"),
        "CODEX_HOME": os.environ.get("CODEX_HOME"),
        "TERMINAL_CWD": os.environ.get("TERMINAL_CWD"),
        "FAKE_TEST_MARKER": os.environ.get("FAKE_TEST_MARKER"),
    },
    "full_env": dict(os.environ),
}
calls = []
if calls_file.exists():
    try:
        calls = json.loads(calls_file.read_text(encoding="utf-8"))
    except Exception:
        pass
calls.append(call_info)
calls_file.write_text(json.dumps(calls, indent=2), encoding="utf-8")

# Check if delay requested
delay = float(config.get("delay_s", 0))
if delay > 0:
    time.sleep(delay)

# Handle hermes --usage-file if specified in argv
if "--usage-file" in sys.argv:
    idx = sys.argv.index("--usage-file")
    if idx + 1 < len(sys.argv):
        usage_path = Path(sys.argv[idx + 1])
        usage_data = config.get("usage_file_data")
        if usage_data is not None:
            usage_path.write_text(json.dumps(usage_data), encoding="utf-8")

# Write stderr and stdout
stderr = config.get("stderr", "")
if stderr:
    sys.stderr.write(stderr)
    sys.stderr.flush()

stdout = config.get("stdout", "")
if stdout:
    sys.stdout.write(stdout)
    sys.stdout.flush()

sys.exit(int(config.get("exit_code", 0)))
"""
        self.runner_script.write_text(code.strip(), encoding="utf-8")

    def register_cli(self, name: str) -> Path:
        """Create executable wrapper for the given CLI name in self.bin_dir."""
        if sys.platform == "win32":
            cmd_file = self.bin_dir / f"{name}.cmd"
            cmd_content = f'@"{sys.executable}" "{self.runner_script}" %*\n'
            cmd_file.write_text(cmd_content, encoding="utf-8")
            return cmd_file
        else:
            sh_file = self.bin_dir / name
            sh_content = f'#!/bin/sh\nexec "{sys.executable}" "{self.runner_script}" "$@"\n'
            sh_file.write_text(sh_content, encoding="utf-8")
            sh_file.chmod(0o755)
            return sh_file

    def set_response(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        delay_s: float = 0.0,
        usage_file_data: dict[str, Any] | None = None,
    ) -> None:
        """Configure what the fake CLI should output on next calls."""
        data = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "delay_s": delay_s,
            "usage_file_data": usage_file_data,
        }
        self.config_file.write_text(json.dumps(data), encoding="utf-8")

    def get_calls(self) -> list[dict[str, Any]]:
        """Return all calls recorded by the fake CLI."""
        if not self.calls_file.exists():
            return []
        try:
            return json.loads(self.calls_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def clear_calls(self) -> None:
        """Clear recorded calls."""
        if self.calls_file.exists():
            self.calls_file.unlink()

    def cleanup(self) -> None:
        """Clean up temporary directory if managed."""
        if self._temp_dir_obj is not None:
            self._temp_dir_obj.cleanup()
