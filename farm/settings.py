import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def env_file() -> Path:
    """The ``.env`` the Farm reads (and ``farm set-secret`` writes): ``FARM_ENV_FILE`` if set, else ``.env``
    in the repository root. A server sets it to keep its secrets out of the read-only code checkout."""
    override = os.environ.get("FARM_ENV_FILE")
    return Path(override) if override else DEFAULT_ENV_PATH


def load_env(path: Path | None = None) -> None:
    path = env_file() if path is None else path
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                key, sep, val = stripped.partition("=")
                if not sep:
                    continue
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and (
                    (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))
                ):
                    val = val[1:-1]
                if key not in os.environ:
                    os.environ[key] = val
    from farm.secrets import register_env_secrets

    register_env_secrets()


def require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"missing env var {name}")
    return val


def default_data_dir() -> Path:
    """Where the Farm keeps its state when FARM_DATA_DIR is not set: ``D:/farm-data`` on Windows (the owner's
    PC), ``$XDG_DATA_HOME/harness-farm`` (``~/.local/share/harness-farm``) elsewhere. A server sets
    FARM_DATA_DIR (``/var/lib/harness-farm``, see ``deploy/farm.env.example``)."""
    if sys.platform == "win32":
        return Path("D:/farm-data")
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "harness-farm"


def data_dir_path() -> Path:
    """FARM_DATA_DIR, else :func:`default_data_dir`; neither read from ``.env`` nor created (see
    :func:`data_dir`)."""
    raw = os.environ.get("FARM_DATA_DIR")
    return Path(raw) if raw else default_data_dir()


def data_dir() -> Path:
    """Return the Farm data directory (FARM_DATA_DIR, else :func:`default_data_dir`), created on demand."""
    load_env()
    path = data_dir_path()
    path.mkdir(parents=True, exist_ok=True)
    return path


HTTP_HOST: str = "127.0.0.1"
HTTP_PORT: int = 8787
HTTP_PATH: str = "/mcp"


def http_host() -> str:
    load_env()
    return os.environ.get("FARM_HTTP_HOST", HTTP_HOST)


def http_port() -> int:
    load_env()
    return int(os.environ.get("FARM_HTTP_PORT", str(HTTP_PORT)))


def http_path() -> str:
    load_env()
    return os.environ.get("FARM_HTTP_PATH", HTTP_PATH)


class _HttpSetting(str):
    def __call__(self) -> str:
        return str(self)


class _HttpPortSetting(int):
    def __call__(self) -> int:
        return int(self)


def __getattr__(name: str) -> Any:
    if name == "http_host":
        return _HttpSetting(http_host())
    if name == "http_port":
        return _HttpPortSetting(http_port())
    if name == "http_path":
        return _HttpSetting(http_path())
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
