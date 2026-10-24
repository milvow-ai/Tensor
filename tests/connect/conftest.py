"""Sign-in tests register the fake codes and keys they paste as secrets (``farm.secrets.register_secret``), and
``redact`` then masks those strings in every later message of the process. Forget them after each test, so a fake
value can never mask text another test checks (``wrong`` once masked Hunter's ``wrong_params`` error id)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from farm import secrets


@pytest.fixture(autouse=True)
def _forget_secrets_registered_by_the_test() -> Iterator[None]:
    before = set(secrets._seen)
    yield
    with secrets._lock:
        secrets._seen.intersection_update(before)
