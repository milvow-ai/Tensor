"""The ``farm`` commands added in M1c, run for real against a throw-away migrated database."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import respx
import structlog
import yaml
from alembic import command
from typer.testing import CliRunner

from farm.control.cli import alembic_config, app
from farm.registry import load_registry
from tests.conftest import FIXTURE_REGISTRY, REOON_URL, ZEROBOUNCE_URL, reoon_body, zerobounce_body
from tests.farm_helpers import EMAIL, normalise

runner = CliRunner()


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()  # the commands configure structlog for stderr; do not leak that into other tests


@pytest.fixture
def migrated_db(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, provider_keys: None
) -> Iterator[str]:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    yield url


def farm(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.stdout, result.stderr


def test_registry_schema_prints_the_json_schema() -> None:
    code, out, _ = farm("registry", "schema")
    schema = json.loads(out)
    assert (
        code == 0
        and schema["$schema"].startswith("https://json-schema.org/")
        and "providers" in schema["properties"]
    )


def test_sync_is_idempotent_and_dry_run_writes_nothing(migrated_db: str) -> None:
    code, out, _ = farm("registry", "sync", str(FIXTURE_REGISTRY), "--dry-run")
    assert code == 0 and "would change" in out and "dry run: nothing was written" in out
    assert "no pools" in farm("status")[1]  # nothing was written

    code, out, _ = farm("registry", "sync", str(FIXTURE_REGISTRY))
    assert code == 0 and "changed" in out and "created:" in out and "providers 4" in out

    code, out, _ = farm("registry", "sync", str(FIXTURE_REGISTRY))
    assert code == 0 and "changed 0 row(s)" in out and "already matches" in out


def test_export_is_the_registry_as_the_database_holds_it(migrated_db: str, tmp_path: Path) -> None:
    farm("registry", "sync", str(FIXTURE_REGISTRY))

    code, out, _ = farm("registry", "export")
    assert code == 0
    exported = tmp_path / "exported.yaml"
    exported.write_text(out, encoding="utf-8")
    assert yaml.safe_load(out)["providers"]["reoon"]["connections"][0]["units"]["credits"]["limit"] == 20
    assert normalise(load_registry(exported)) == normalise(load_registry(FIXTURE_REGISTRY))

    code, out, _ = farm("registry", "sync", str(exported))  # export -> sync is a no-op
    assert code == 0 and "already matches" in out


def test_export_to_a_file(migrated_db: str, tmp_path: Path) -> None:
    farm("registry", "sync", str(FIXTURE_REGISTRY))
    target = tmp_path / "out.yaml"
    code, out, _ = farm("registry", "export", str(target))
    assert code == 0 and str(target) in out and load_registry(target)


def test_status_shows_every_unit_and_when_it_resets(migrated_db: str) -> None:
    farm("registry", "sync", str(FIXTURE_REGISTRY))

    code, out, _ = farm("status")

    assert code == 0
    lines = out.splitlines()
    assert lines[0].split() == ["POOL", "CONNECTION", "STATE", "UNIT", "USED/LIMIT", "REMAINING", "RESETS"]
    reoon = next(line for line in lines if "reoon-01" in line)
    assert reoon.split()[:2] == ["reoon", "reoon-01"] and "0/20" in reoon and "20" in reoon
    assert any("zerobounce-01" in line and "0/100" in line for line in lines)
    clay_rows = [
        line for line in lines if "clay-01" in line or line.lstrip().startswith(("credits", "actions"))
    ]
    assert len(clay_rows) >= 2  # a connection with two units gets a row per unit
    only_route = farm("status", "verify_email")[1]
    assert "clay" not in only_route and only_route.index("reoon") < only_route.index("zerobounce")


def test_call_runs_a_capability_end_to_end_and_prints_the_envelope(
    migrated_db: str, http: respx.MockRouter
) -> None:
    farm("registry", "sync", str(FIXTURE_REGISTRY))
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    code, out, _ = farm("call", "verify_email", "--params", json.dumps({"email": EMAIL}))

    envelope = json.loads(out)
    assert code == 0 and reoon.call_count == 1
    assert envelope["ok"] is True and envelope["source"]["connection_id"] == "reoon-01"
    assert envelope["cost"]["units"] == {"credits": 1.0}
    again = json.loads(farm("call", "verify_email", "--params", json.dumps({"email": EMAIL}))[1])
    assert again["source"]["cached"] is True and reoon.call_count == 1  # the cache lives in the database
    assert "reoon-01" in farm("status")[1]


def test_call_exits_nonzero_when_the_capability_failed(migrated_db: str, http: respx.MockRouter) -> None:
    farm("registry", "sync", str(FIXTURE_REGISTRY))
    http.get(REOON_URL).respond(500, json={"status": "error", "reason": "down"})
    http.get(ZEROBOUNCE_URL).respond(500, json={"error": "down"})

    code, out, _ = farm("call", "verify_email", "--params", json.dumps({"email": EMAIL}))

    assert code == 1 and json.loads(out)["error"]["kind"] == "server"


def test_call_pin_and_strategy_options_reach_the_router(migrated_db: str, http: respx.MockRouter) -> None:
    farm("registry", "sync", str(FIXTURE_REGISTRY))
    http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    code, out, _ = farm("call", "verify_email", "--params", json.dumps({"email": EMAIL}), "--pin", "reoon-02")

    assert code == 0 and json.loads(out)["source"]["connection_id"] == "reoon-02"


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["call", "verify_email", "--params", "{not json"], 2, "not valid JSON"),
        (["call", "verify_email", "--params", "[1]"], 2, "must be a JSON object"),
        (["call", "no_such_capability"], 1, "unknown capability"),
        (["registry", "sync", "does-not-exist.yaml"], 1, "cannot read registry file"),
    ],
)
def test_user_mistakes_are_one_clear_line_not_a_traceback(
    migrated_db: str, args: list[str], code: int, message: str
) -> None:
    got_code, out, err = farm(*args)
    assert got_code == code and message in err and "Traceback" not in out + err


def test_a_database_without_the_schema_says_what_to_run(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FARM_DB_URL", scratch_db())  # an empty database: never migrated

    code, _, err = farm("status")

    assert code == 1 and "farm db migrate" in err and "Traceback" not in err


def test_logs_go_to_stderr_never_to_stdout(migrated_db: str, http: respx.MockRouter) -> None:
    """stdout is the MCP stream for ``serve`` and the JSON envelope for ``call``: a log line there breaks both."""
    farm("registry", "sync", str(FIXTURE_REGISTRY))
    http.get(REOON_URL).respond(
        500, json={"status": "error", "reason": "down"}
    )  # makes the adapter log a warning
    http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    code, out, err = farm("call", "verify_email", "--params", json.dumps({"email": EMAIL}))

    assert code == 0 and json.loads(out)["ok"] is True  # stdout is exactly one JSON document
    assert "adapter.failure" in err
