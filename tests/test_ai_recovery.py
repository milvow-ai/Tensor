"""Focused authorization regressions for explicit AI recovery."""

from uuid import uuid4

import pytest

from farm.gateway.ai_tools import _recovery_approval_actor
from farm.gateway.middleware import current_caller, current_identity
from farm.tenancy import TokenIdentity


def test_local_operator_can_approve_provider_change() -> None:
    token = current_identity.set(None)
    try:
        assert _recovery_approval_actor() == (True, "local:mcp")
    finally:
        current_identity.reset(token)


@pytest.mark.parametrize(
    "scopes, allowed",
    [(('*',), True), (("ai:recovery:approve",), True), (("internal",), False), (("worker",), False)],
)
def test_token_recovery_approval_uses_authenticated_scope(scopes: tuple[str, ...], allowed: bool) -> None:
    identity = TokenIdentity(
        token_id=uuid4(), tenant_id=uuid4(), user_id=None, name="worker", scopes=scopes
    )
    identity_token = current_identity.set(identity)
    caller_token = current_caller.set("spoofed-operator")
    try:
        approved, actor = _recovery_approval_actor()
        assert approved is allowed
        assert actor.startswith("token:")
        assert "spoofed-operator" not in actor
    finally:
        current_identity.reset(identity_token)
        current_caller.reset(caller_token)
