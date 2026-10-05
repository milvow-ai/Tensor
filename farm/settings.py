import os
from pathlib import Path
from typing import Any

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = DEFAULT_ENV_PATH) -> None:
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


def data_dir() -> Path:
    """Return the Farm data directory (env FARM_DATA_DIR, default D:/farm-data), created on demand."""
    load_env()
    path = Path(os.environ.get("FARM_DATA_DIR") or "D:/farm-data")
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
