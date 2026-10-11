"""Token requests authenticate the client one way only (Sentry rejects Basic header + client_id in the body)."""

from farm.executors.mcp.connect import single_client_auth


def _basic(
    data: dict[str, str], headers: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    headers = dict(headers or {})
    headers["Authorization"] = "Basic Y2xpZW50OnNlY3JldA=="
    return {k: v for k, v in data.items() if k != "client_secret"}, headers


def _post(
    data: dict[str, str], headers: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    return {**data, "client_secret": "secret"}, dict(headers or {})


def test_basic_auth_drops_client_id_from_body() -> None:
    data, headers = single_client_auth(_basic)(
        {"grant_type": "authorization_code", "client_id": "client"}, {}
    )
    assert headers["Authorization"].startswith("Basic ")
    assert "client_id" not in data
    assert data["grant_type"] == "authorization_code"


def test_post_and_public_clients_keep_client_id() -> None:
    data, _ = single_client_auth(_post)({"client_id": "client"}, {})
    assert data["client_id"] == "client"
    public = single_client_auth(lambda d, h=None: (d, dict(h or {})))
    data, headers = public({"client_id": "client"}, {})
    assert data["client_id"] == "client" and "Authorization" not in headers
