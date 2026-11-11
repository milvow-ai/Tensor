#!/usr/bin/env python3
"""Validate and provision isolated profiles; never start services or print credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

APP = Path("/opt/harness-farm")
ETC = Path("/etc/harness-farm/instances")
STATE = Path("/var/lib/harness-farm-instances")
UI_STATE = Path("/var/lib/harness-farm-consoles")
NAME = re.compile(r"[a-z][a-z0-9-]{0,19}\Z")
KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")


class ProfileError(ValueError):
    pass


def fail(message: str) -> None:
    raise ProfileError(message)


def source(path: str) -> Path:
    item = Path(path)
    if not item.is_absolute() or item.is_symlink() or not item.is_file():
        fail("profile input must be an absolute regular file, not a symlink")
    if os.name == "posix" and item.stat().st_mode & 0o077:
        fail("profile inputs must have private permissions (chmod 600)")
    return item


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not KEY.fullmatch(key) or key in result:
            fail("invalid or duplicate environment key")
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        if any(ord(c) < 32 for c in value):
            fail("environment values must not contain control characters")
        result[key] = value
    return result


def validate(manifest: Path) -> list[dict[str, object]]:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or not profiles or len(profiles) > 16:
        fail("profiles must contain 1–16 explicitly configured instances")
    names, ports, databases, projects, hosts = set(), set(), set(), set(), set()
    checked = []
    for profile in profiles:
        if not isinstance(profile, dict):
            fail("invalid profile")
        name, port = profile.get("name"), profile.get("port")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            fail("profile names must be unique lowercase slugs of at most 20 characters")
        if type(port) is not int or not 1024 <= port <= 65535 or port in ports:
            fail("profile ports must be distinct integers from 1024 to 65535")
        if profile.get("kind") not in ("personal", "shared"):
            fail("profile kind must be personal or shared")
        console_port = profile.get("console_port", port + 100)
        if type(console_port) is not int or not 1024 <= console_port <= 65535:
            fail("console_port must be an integer from 1024 to 65535")
        if console_port == port or console_port in ports:
            fail("console and Farm ports must all be distinct")
        public = urlsplit(str(profile.get("public_url", "")))
        if (
            public.scheme != "https"
            or not public.hostname
            or public.username is not None
            or public.password is not None
            or public.query
            or public.fragment
        ):
            fail("public_url must be HTTPS without credentials, query or fragment")
        if public.path != "/mcp":
            fail("public_url must use the /mcp path")
        if any(ord(c) < 33 for c in str(profile["public_url"])):
            fail("invalid public URL")
        if public.hostname in hosts:
            fail("personal consoles need distinct hostnames to isolate authentication cookies")
        secrets = source(str(profile.get("secrets", "")))
        registry = source(str(profile.get("registry", "")))
        env = read_env(secrets)
        raw_db = env.get("FARM_DB_URL") or env.get("SUPABASE_DB_URL") or ""
        db = urlsplit(raw_db)
        if (
            db.scheme not in ("postgres", "postgresql")
            or not db.hostname
            or not db.username
            or not db.password
        ):
            fail("every profile needs an explicit authenticated PostgreSQL URL; embedded trust is forbidden")
        if not db.path.strip("/"):
            fail("database name must be explicit")
        if db.hostname.endswith(".pooler.supabase.com") and db.port == 6543:
            fail("use a direct or session-mode database connection, not a transaction-mode pooler")
        if parse_qs(db.query).get("sslmode", [""])[0] not in ("require", "verify-ca", "verify-full"):
            fail("database URL must require TLS")
        project = urlsplit(env.get("SUPABASE_URL", ""))
        if (
            project.scheme != "https"
            or not project.hostname
            or project.username is not None
            or project.password is not None
            or project.query
            or project.fragment
        ):
            fail("each dashboard profile needs its own HTTPS Supabase-compatible project")
        if not env.get("SUPABASE_SERVICE_ROLE_KEY"):
            fail("profile is missing its own Supabase service key for member onboarding")
        publishable = public_key(env)
        identity = f"{db.hostname}:{db.port or 5432}{db.path}"
        if db.hostname.endswith(".pooler.supabase.com"):
            identity += f":{db.username}"
        project_id = project.geturl().rstrip("/")
        if identity in databases or project_id in projects:
            fail("profiles must not share a database or Supabase project")
        names.add(name)
        ports.add(port)
        ports.add(console_port)
        hosts.add(public.hostname)
        databases.add(identity)
        projects.add(project_id)
        checked.append(
            {
                **profile,
                "console_port": console_port,
                "console_public": {"url": project.geturl().rstrip("/"), "key": publishable},
                "db_fingerprint": hashlib.sha256(identity.encode()).hexdigest(),
                "secrets_path": secrets,
                "registry_path": registry,
            }
        )
    return checked


def run(*args: str) -> None:
    commands = {"useradd": "/usr/sbin/useradd", "systemctl": "/usr/bin/systemctl"}
    subprocess.run(
        (commands[args[0]], *args[1:]),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )


def public_key(env: dict[str, str]) -> str:
    value = env.get("SUPABASE_ANON_KEY") or env.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or ""
    if not value or value == env.get("SUPABASE_SERVICE_ROLE_KEY") or value.startswith("sb_secret_"):
        fail("a separate publishable/anon key is required for the console; never use a service key")
    if value.count(".") == 2:
        import base64

        try:
            part = value.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            if claims.get("role") != "anon":
                fail("console JWT must have the anon role")
        except ProfileError:
            raise
        except Exception:
            fail("invalid console JWT")
    return value


def private_directory(path: Path, uid: int, gid: int) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o700)
        os.fchown(fd, uid, gid)
    finally:
        os.close(fd)


def private_file(path: Path, content: bytes, uid: int, gid: int) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        created = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fail("managed file is not a private regular file")
        if not created:
            if info.st_uid != uid:
                fail("managed file has an unexpected owner")
            with os.fdopen(os.dup(fd), "rb") as current:
                if current.read() != content:
                    fail("managed configuration differs; stop and review a deliberate update")
        else:
            with os.fdopen(os.dup(fd), "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
    finally:
        os.close(fd)


def provision(profiles: list[dict[str, object]]) -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        fail("apply requires root on the target Linux server")
    if not (APP / ".venv/bin/farm").is_file() or (APP / ".env").exists():
        fail("install a clean read-only code checkout and venv first; no repository .env is allowed")
    import pwd

    for base in (STATE, UI_STATE, ETC):
        base.mkdir(parents=True, exist_ok=True, mode=0o755)
        if base.is_symlink() or base.stat().st_uid != 0 or base.stat().st_mode & 0o022:
            fail("instance parents must be root-controlled directories")
    for p in profiles:
        for key in ("secrets_path", "registry_path"):
            path = Path(str(p[key]))
            if path.stat().st_uid != 0 or any(
                parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022 for parent in path.parents
            ):
                fail("apply inputs must be staged in a root-owned private directory")

    # Preflight every profile before making any changes. Existing configs are never overwritten.
    identities: set[int] = set()
    for p in profiles:
        for prefix in ("hf-", "hfui-"):
            try:
                existing = pwd.getpwnam(prefix + str(p["name"]))
                if existing.pw_uid == 0 or existing.pw_uid in identities:
                    fail("every Farm and console needs a distinct non-root OS identity")
                identities.add(existing.pw_uid)
            except KeyError:
                pass
    for p in profiles:
        name = str(p["name"])
        meta = ETC / name / "profile.json"
        for path in (
            STATE,
            STATE / name,
            ETC,
            ETC / name,
            meta,
            *(
                STATE / name / item
                for item in ("workspaces", "logs", "ai", "tokens", "secrets.env", "registry.yaml")
            ),
        ):
            if path.is_symlink():
                fail("symlink in managed instance paths; refusing privileged file operations")
        if (STATE / name).exists() and not meta.is_file():
            fail("unmanaged state directory exists; refusing to adopt it")
        if meta.exists() and json.loads(meta.read_text()) != {
            k: p[k] for k in ("name", "kind", "port", "console_port", "public_url", "db_fingerprint")
        }:
            fail("existing profile differs; stop and review a deliberate configuration migration")
        for target, key in (
            (STATE / name / "secrets.env", "secrets_path"),
            (STATE / name / "registry.yaml", "registry_path"),
        ):
            if target.exists() and target.read_bytes() != Path(str(p[key])).read_bytes():
                fail("existing profile configuration differs; no files were overwritten")
    for p in profiles:
        name, user = str(p["name"]), f"hf-{p['name']}"
        state, settings = STATE / name, ETC / name
        try:
            account = pwd.getpwnam(user)
            if account.pw_dir != str(state) or not (settings / "profile.json").is_file():
                fail("existing service user is not this managed instance")
        except KeyError:
            run(
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(state),
                "--shell",
                "/usr/sbin/nologin",
                user,
            )
            account = pwd.getpwnam(user)
        settings.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.chmod(0o700)
        for folder in (state, *(state / item for item in ("workspaces", "logs", "ai", "tokens", "backups"))):
            private_directory(folder, account.pw_uid, account.pw_gid)
        for key, file in (("secrets_path", "secrets.env"), ("registry_path", "registry.yaml")):
            target = state / file
            private_file(target, Path(str(p[key])).read_bytes(), account.pw_uid, account.pw_gid)
        ui_user, ui_state = f"hfui-{name}", UI_STATE / name
        try:
            ui = pwd.getpwnam(ui_user)
            if ui.pw_dir != str(ui_state):
                fail("existing console user has a different home")
        except KeyError:
            run(
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(ui_state),
                "--shell",
                "/usr/sbin/nologin",
                ui_user,
            )
            ui = pwd.getpwnam(ui_user)
        private_directory(ui_state, ui.pw_uid, ui.pw_gid)
        private_file(
            ui_state / "public-settings.json", json.dumps(p["console_public"]).encode(), ui.pw_uid, ui.pw_gid
        )
        runtime = {
            "HOME": str(state),
            "FARM_DATA_DIR": str(state),
            "FARM_ENV_FILE": str(state / "secrets.env"),
            "FARM_REGISTRY_PATH": str(state / "registry.yaml"),
            "FARM_HTTP_HOST": "127.0.0.1",
            "FARM_HTTP_PORT": str(p["port"]),
            "FARM_HTTP_PATH": "/mcp",
            "FARM_PUBLIC_URL": str(p["public_url"]),
            "FARM_MAX_PARALLEL": "2",
            "FARM_INSTANCE_ID": name,
            "FARM_REPORTING_ENABLED": "1",
            "FARM_SHARED_INSTANCE": "1" if p["kind"] == "shared" else "0",
            "FARM_REQUIRE_EXTERNAL_DB": "1",
            "FARM_EXPECT_STATE_BACKUP": "1",
            "FARM_EXPECT_OFFSITE_BACKUP": "1",
            "LANG": "C.UTF-8",
            "PATH": f"{state}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        }
        (settings / "runtime.env").write_text("".join(f"{k}={v}\n" for k, v in runtime.items()))
        (settings / "runtime.env").chmod(0o600)
        (settings / "profile.json").write_text(
            json.dumps(
                {k: p[k] for k in ("name", "kind", "port", "console_port", "public_url", "db_fingerprint")},
                indent=2,
            )
            + "\n"
        )
    shutil.copyfile(APP / "deploy/systemd/harness-farm@.service", "/etc/systemd/system/harness-farm@.service")
    for unit in ("harness-farm-backup@.service", "harness-farm-backup@.timer"):
        shutil.copyfile(APP / "deploy/systemd" / unit, Path("/etc/systemd/system") / unit)
    shutil.copyfile(APP / "scripts/linux/farm-instance", "/usr/local/bin/farm-instance")
    Path("/usr/local/bin/farm-instance").chmod(0o755)
    run("systemctl", "daemon-reload")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="Provision on target server; never start services"
    )
    args = parser.parse_args()
    try:
        profiles = validate(args.manifest)
        if args.apply:
            provision(profiles)
        for profile in profiles:
            print(f"{profile['name']}: {profile['kind']}, loopback port {profile['port']}")
        print(
            "No services started. Verify DB isolation, migrate, onboard and configure private TLS "
            "before starting explicitly."
        )
    except Exception as exc:
        # Never print exception values: parsers/subprocesses may include credentials from supplied files.
        print(f"Provisioning refused ({type(exc).__name__}).", file=sys.stderr)
        if isinstance(exc, ProfileError):
            print(str(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
