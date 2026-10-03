import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.is_file():
        return
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


def require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"missing env var {name}")
    return val
