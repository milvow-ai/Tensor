"""The names pass-through tools carry on the Farm's MCP surface: ``<namespace>__<tool>``.

Every MCP client accepts names of ``[A-Za-z0-9_-]`` up to 64 characters (Claude's API enforces exactly that),
while servers may use dots or be long. FastMCP's ``Namespace`` transform prefixes names but neither
sanitises nor shortens them, so the mapping is a pure function here: readable when the remote name already
fits, otherwise cut and tagged with a short hash of the original name so it stays unique and stable.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable

SEPARATOR = "__"
MAX_NAME_LENGTH = 64
_VALID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_HASH_LENGTH = 8


def _hashed(namespace: str, remote_name: str) -> str:
    tag = hashlib.sha256(remote_name.encode()).hexdigest()[:_HASH_LENGTH]
    stem = f"{namespace}{SEPARATOR}{_UNSAFE.sub('_', remote_name)}"
    return f"{stem[: MAX_NAME_LENGTH - _HASH_LENGTH - 1]}_{tag}"


def _plain(namespace: str, remote_name: str) -> str:
    name = f"{namespace}{SEPARATOR}{_UNSAFE.sub('_', remote_name)}"
    return name if _VALID.fullmatch(name) else _hashed(namespace, remote_name)


def exposed_names(namespace: str, remote_names: Iterable[str]) -> dict[str, str]:
    """Remote tool name -> the name it is exposed under, unique within the namespace.

    ``create_issue`` becomes ``linear__create_issue``; ``search.pages`` becomes ``notion__search_pages``; a
    name that does not fit in 64 characters, or that sanitises to the same text as another tool, gets an
    eight-character hash of its original name instead of the tail.
    """
    names = sorted(set(remote_names))
    plain = {name: _plain(namespace, name) for name in names}
    taken = Counter(plain.values())
    return {name: plain[name] if taken[plain[name]] == 1 else _hashed(namespace, name) for name in names}
