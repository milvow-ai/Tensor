"""Secret resolution and redaction.

Connections never hold secret values, only an ``auth_ref``:

* ``env:NAME``         read from the process environment (the only scheme implemented in M1)
* ``token-store:ID``   later milestone
* ``cli:PROFILE``      later milestone (a CLI agent logged in under its own profile)

``resolve_auth`` is called at call time only. Every value it returns is remembered in this process so
``redact`` can mask it wherever it turns up (error strings, log records, exception text). Nothing in this
module ever puts a secret value, or an unparsable ``auth_ref`` (which might *be* a pasted secret), into an
exception message.

This module never reads ``.env`` itself; ``farm.settings.load_env`` does that at start-up.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote

__all__ = [
    "AUTH_SCHEMES",
    "AuthRefError",
    "RedactingFilter",
    "ensure_token_store_dir",
    "install_log_redaction",
    "parse_auth_ref",
    "redact",
    "register_env_secrets",
    "register_secret",
    "resolve_auth",
    "resolve_token_store",
    "set_secret",
]

MASK = "***"
AUTH_SCHEMES: tuple[str, ...] = ("env", "token-store", "cli")

# Values shorter than this are not remembered: masking a 1-3 character "secret" would shred every
# error message that happens to contain those characters. Real keys are far longer.
_MIN_SECRET_LEN = 4

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# key=…, api_key=…, apikey=…, token=…, access_token=… inside URLs, query strings or form bodies.
_QUERY_SECRET = re.compile(
    r"(?P<name>\b(?:access[_-]?token|api[_-]?key|token|key)=)(?P<value>[^&\s\"'<>#]+)",
    re.IGNORECASE,
)
# "Authorization: Bearer <token>" — long tokens only, so prose like "bearer of bad news" is left alone.
_BEARER = re.compile(r"(?P<name>\bbearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{16,})", re.IGNORECASE)

_lock = threading.Lock()
_seen: set[str] = set()


class AuthRefError(RuntimeError):
    """An ``auth_ref`` could not be parsed or resolved. The message never contains a secret value."""


def register_secret(value: str) -> None:
    """Remember a secret value so :func:`redact` masks it from now on."""
    if len(value) >= _MIN_SECRET_LEN:
        with _lock:
            _seen.add(value)


def parse_auth_ref(auth_ref: str) -> tuple[str, str]:
    """Split ``scheme:ref`` and validate its shape without resolving it.

    The error text deliberately does not echo ``auth_ref``: someone may have pasted a raw key there.
    """
    scheme, sep, ref = auth_ref.partition(":")
    if not sep or scheme not in AUTH_SCHEMES or not ref:
        raise AuthRefError(
            "auth_ref must look like 'env:NAME', 'token-store:ID' or 'cli:PROFILE' (value not shown)"
        )
    if scheme == "env":
        if not _ENV_NAME.fullmatch(ref):
            raise AuthRefError("auth_ref 'env:' must be followed by a valid environment variable name")
    elif not _REF_ID.fullmatch(ref):
        raise AuthRefError(f"auth_ref '{scheme}:' must be followed by a plain identifier")
    return scheme, ref


def resolve_auth(auth_ref: str, *, allow_token_store: bool = False) -> str:
    """Return the secret an ``auth_ref`` points at (or directory path for ``token-store:ID``).

    Raises :class:`AuthRefError` for anything missing, empty, malformed or not implemented yet.
    """
    scheme, ref = parse_auth_ref(auth_ref)
    if scheme == "token-store" and allow_token_store:
        return str(resolve_token_store(auth_ref))
    if scheme != "env":
        raise AuthRefError(f"auth_ref scheme '{scheme}:' is not supported yet")
    raw = os.environ.get(ref)
    if raw is None:
        raise AuthRefError(f"environment variable {ref} is not set (auth_ref 'env:{ref}')")
    value = raw.strip()
    if not value:
        raise AuthRefError(f"environment variable {ref} is empty (auth_ref 'env:{ref}')")
    register_secret(value)
    return value


def _variants(value: str) -> list[str]:
    """The value as it may appear in text: raw, and percent-encoded the way URLs carry it."""
    return list({value, quote(value, safe=""), quote_plus(value)})


def redact(text: str) -> str:
    """Mask every secret this process has resolved and any ``key=``/``token=`` style value in ``text``.

    Use it on every string that may end up in a log, a result or an error and could contain a URL.
    """
    if not text:
        return text
    with _lock:
        known = sorted(_seen, key=len, reverse=True)
    for secret in known:
        for variant in _variants(secret):
            if variant in text:
                text = text.replace(variant, MASK)
    text = _QUERY_SECRET.sub(lambda m: f"{m.group('name')}{MASK}", text)
    return _BEARER.sub(lambda m: f"{m.group('name')}{MASK}", text)


class RedactingFilter(logging.Filter):
    """Logging filter that runs :func:`redact` over the fully formatted message of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed log call must not be made worse by the filter
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def install_log_redaction(logger_name: str) -> None:
    """Attach a :class:`RedactingFilter` to ``logger_name`` (idempotent).

    ``httpx`` logs ``HTTP Request: GET <full url>`` at INFO, and both email-verification providers carry
    their API key in the query string, so the adapter template installs this on the ``httpx`` logger.
    """
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())


def resolve_token_store(auth_ref: str, *, data_dir: Path | None = None) -> Path:
    """Resolve a ``token-store:<id>`` auth_ref to its token directory path.

    Returns the directory path under FARM_DATA_DIR/tokens/<id> (or data_dir/tokens/<id>).
    Never returns, logs, or stores secret token contents.
    """
    scheme, ref = parse_auth_ref(auth_ref)
    if scheme != "token-store":
        raise AuthRefError(f"expected 'token-store:' auth_ref, got '{scheme}:'")
    base_data_dir = (
        data_dir if data_dir is not None else Path(os.environ.get("FARM_DATA_DIR", "D:/farm-data"))
    )
    tokens_root = (base_data_dir / "tokens").resolve()
    store_dir = (tokens_root / ref).resolve()
    try:
        store_dir.relative_to(tokens_root)
    except ValueError:
        raise AuthRefError(
            "Path traversal detected in auth_ref: token-store path escapes tokens root"
        ) from None
    if store_dir == tokens_root:
        raise AuthRefError("Invalid auth_ref: token-store points to tokens root")
    return ensure_token_store_dir(store_dir)


def ensure_token_store_dir(store_dir: Path) -> Path:
    """Create token store directory with restricted ACL (current user only) if feasible."""
    store_dir.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            import subprocess

            user = os.environ.get("USERNAME")
            if user:
                subprocess.run(
                    ["icacls", str(store_dir), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                    capture_output=True,
                    check=False,
                )
        else:
            os.chmod(store_dir, 0o700)
    except Exception:
        pass
    return store_dir


_SECRET_ENV_NAME = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|_VK\b|\bVK\b|AUTH|CREDENTIAL)",
    re.IGNORECASE,
)
_DB_URL_PW = re.compile(
    r"^[a-z][a-z0-9+.-]*://[^:/\s]*:([^@\s]+)@",
    re.IGNORECASE,
)


def register_env_secrets() -> None:
    """Inspect environment variables and register all secret-looking values for redaction."""
    for key, value in os.environ.items():
        if not value:
            continue
        if _SECRET_ENV_NAME.search(key):
            register_secret(value)

        val = value.strip()
        match = _DB_URL_PW.search(val)
        if match:
            pw = match.group(1)
            register_secret(pw)
            unquoted = unquote(pw)
            if unquoted != pw:
                register_secret(unquoted)





def _env_file_value(value: str) -> str:
    """The text after ``NAME=`` that ``farm.settings.load_env`` reads back as exactly ``value``.

    The loader strips surrounding whitespace and one pair of matching quotes, so a value that would not
    survive that is wrapped in the quote character it does not contain.
    """
    fragile = value != value.strip() or (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'")
    if not fragile:
        return value
    for quote_char in ('"', "'"):
        if quote_char not in value:
            return f"{quote_char}{value}{quote_char}"
    raise ValueError("this value cannot be stored in a .env file (it contains both kinds of quote)")


def set_secret(name: str, value: str, *, env_path: Path | None = None) -> Path:
    """Write ``name=value`` into the local ``.env`` (the line is replaced when it exists) and return its path.

    This is the one way the Farm writes a credential (``farm set-secret`` and ``farm mcp import`` both come
    here): the value is never printed or logged, is registered so :func:`redact` masks it from now on, and
    the file is replaced atomically so a crash cannot leave a half-written ``.env``. Other lines, comments
    and the file's line endings are kept. Nothing is done to the process environment.
    """
    if not _ENV_NAME.fullmatch(name):
        raise ValueError("the secret's name must be a valid environment variable name")
    if not value.strip():
        raise ValueError(f"the value of {name} is empty")
    if any(char in value for char in "\r\n\0"):
        raise ValueError(f"the value of {name} contains a line break, which a .env file cannot hold")

    from farm.settings import DEFAULT_ENV_PATH

    path = env_path or DEFAULT_ENV_PATH
    existing = path.read_bytes().decode("utf-8") if path.is_file() else ""
    newline = "\r\n" if "\r\n" in existing else "\n"
    lines = existing.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line.removesuffix("\r") for line in lines]

    entry = f"{name}={_env_file_value(value)}"
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.partition("=")[0].strip() == name:
            lines[index] = entry
            replaced = True
    if not replaced:
        lines.append(entry)

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes((newline.join(lines) + newline).encode("utf-8"))
        if os.name != "nt" and not path.exists():
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    register_secret(value)
    return path
