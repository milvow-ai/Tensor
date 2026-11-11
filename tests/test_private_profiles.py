"""Private-team provisioning refuses merged identities and never prints credentials."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/linux/provision-team.py"
spec = importlib.util.spec_from_file_location("provision_team", SCRIPT)
assert spec and spec.loader
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def manifest(tmp: Path) -> Path:
    profiles = []
    for i, name in enumerate(("owner", "member-a", "member-b", "team")):
        secrets = tmp / f"{name}.env"
        secrets.write_text(
            f"SUPABASE_DB_URL=postgresql://postgres:test@db-{i}.example/db?sslmode=require\n"
            f"SUPABASE_URL=https://project-{i}.example\nSUPABASE_SERVICE_ROLE_KEY=private-sentinel\n"
            "SUPABASE_ANON_KEY=public-test-key\n"
        )
        registry = tmp / f"{name}.yaml"
        registry.write_text("providers: {}\ncapabilities: {}\n")
        secrets.chmod(0o600)
        registry.chmod(0o600)
        profiles.append(
            {
                "name": name,
                "kind": "shared" if name == "team" else "personal",
                "port": 8791 + i,
                "public_url": f"https://{name}.example/mcp",
                "secrets": str(secrets),
                "registry": str(registry),
            }
        )
    path = tmp / "manifest.json"
    path.write_text(json.dumps({"profiles": profiles}))
    return path


def test_plan_has_separate_databases_and_does_not_apply_or_print_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = manifest(tmp_path)
    profiles = provision.validate(path)
    assert len({p["db_fingerprint"] for p in profiles}) == 4
    monkeypatch.setattr("sys.argv", [str(SCRIPT), str(path)])
    monkeypatch.setattr(provision, "provision", lambda _: pytest.fail("dry-run performed changes"))
    provision.main()
    output = capsys.readouterr().out
    assert "No services started" in output
    assert "private-sentinel" not in output and "postgresql" not in output


@pytest.mark.parametrize(
    "field,value",
    [("name", "../escape"), ("port", 8791), ("port", True), ("public_url", "http://public.example/mcp")],
)
def test_invalid_names_ports_and_transport_are_rejected(tmp_path: Path, field: str, value: object) -> None:
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    data["profiles"][1][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        provision.validate(path)


def test_reusing_another_persons_database_or_project_is_rejected(tmp_path: Path) -> None:
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    second = Path(data["profiles"][1]["secrets"])
    first = Path(data["profiles"][0]["secrets"])
    second.write_bytes(first.read_bytes())
    with pytest.raises(ValueError, match="must not share"):
        provision.validate(path)


def test_missing_authenticated_database_never_falls_back_to_embedded_trust(tmp_path: Path) -> None:
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    first = Path(data["profiles"][0]["secrets"])
    first.write_text("SUPABASE_URL=https://one.example\nSUPABASE_SERVICE_ROLE_KEY=test\n")
    with pytest.raises(ValueError, match="embedded trust"):
        provision.validate(path)


def test_hosted_transaction_pooler_is_incompatible_with_session_tenant_context(tmp_path: Path) -> None:
    path = manifest(tmp_path)
    profile = json.loads(path.read_text())["profiles"][0]
    env_file = Path(profile["secrets"])
    env_file.write_text(env_file.read_text().replace("db-0.example", "aws-0.example.pooler.supabase.com:6543"))
    with pytest.raises(ValueError, match="session-mode"):
        provision.validate(path)


def test_private_runtime_cannot_fall_back_to_local_trust(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from farm.db import local
    from farm.db.pool import get_db_url

    monkeypatch.setenv("FARM_REQUIRE_EXTERNAL_DB", "1")
    monkeypatch.setenv("FARM_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.delenv("FARM_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setattr(local, "start_local", lambda: pytest.fail("embedded trust server started"))
    with pytest.raises(RuntimeError, match="require an authenticated"):
        get_db_url()
    with pytest.raises(RuntimeError, match="require an authenticated"):
        get_db_url(force_local=True)
    url = "host=127.0.0.1 user=profile dbname=profile password=test sslmode=require"
    monkeypatch.setenv("FARM_DB_URL", url)
    assert get_db_url() == url
