"""Harness Farm doctor: system self-check and diagnostic suite.

Validates the complete operational health of the Farm in one command:
- Database connectivity and Alembic migrations at head
- Bifrost LLM gateway health
- Provider pools have active connections
- Needs-login connections
- Open circuit breakers
- Exhausted accounts and reset times
- Free disk space (>= 2 GB on C: and D:)
- FARM_DATA_DIR writability
- Evidence directory size
- Last backup age (< 26 h)
- Keep-awake state
- Command consumer heartbeat (< 60 s)
- AI CLIs on PATH with version strings
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import psycopg
import structlog
from alembic.config import Config
from alembic.script import ScriptDirectory

from farm.db.pool import DbPool, get_db_url
from farm.executors.cli_agent.base import build_child_env
from farm.secrets import redact
from farm.settings import data_dir as get_data_dir

log = structlog.get_logger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "db" / "alembic.ini"
DEFAULT_BIFROST_URL = "http://127.0.0.1:8080"


@dataclass
class CheckResult:
    """Outcome of a single doctor check."""

    name: str
    status: Literal["PASS", "WARN", "FAIL"]
    detail: str
    fix_hint: str = ""

    def format_line(self) -> str:
        tag = f"{self.status:<4}"
        base = f"{tag}  {self.name}: {self.detail}"
        if self.status != "PASS" and self.fix_hint:
            return f"{base} -> {self.fix_hint}"
        return base


# --- Pure Check Functions ---


def check_db_and_migrations(
    pool: DbPool | None = None,
    db_url: str | None = None,
) -> CheckResult:
    """Check database reachability and whether Alembic migrations are at head."""
    resolved_url = db_url or (get_db_url() if pool is None else None)

    # 1. Determine script head from alembic migrations
    try:
        cfg = Config(str(ALEMBIC_INI))
        cfg.attributes["configure_logger"] = False
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head()
    except Exception as exc:
        return CheckResult(
            name="db_and_migrations",
            status="FAIL",
            detail=f"Failed to inspect Alembic migration scripts: {exc}",
            fix_hint="Check alembic.ini and farm/db/migrations",
        )

    # 2. Check DB connection and alembic_version table
    try:
        if pool is not None:
            # Synchronous check using psycopg.connect for reliable script execution
            target_url = resolved_url or get_db_url()
        else:
            target_url = resolved_url or get_db_url()

        with psycopg.connect(target_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select exists ("
                    "select 1 from information_schema.tables "
                    "where table_schema = 'public' and table_name = 'alembic_version'"
                    ")"
                )
                has_version_table = cur.fetchone()[0]  # type: ignore[index]
                if not has_version_table:
                    return CheckResult(
                        name="db_and_migrations",
                        status="FAIL",
                        detail="Database connected, but alembic_version table is missing",
                        fix_hint="Run 'uv run farm db migrate' to apply migrations",
                    )

                cur.execute("select version_num from public.alembic_version limit 1")
                row = cur.fetchone()
                current_rev = row[0] if row else None

        if current_rev == head_rev:
            return CheckResult(
                name="db_and_migrations",
                status="PASS",
                detail=f"Database connected; migrations at head (rev {head_rev})",
            )
        else:
            return CheckResult(
                name="db_and_migrations",
                status="FAIL",
                detail=f"Database revision is '{current_rev}', but head is '{head_rev}'",
                fix_hint="Run 'uv run farm db migrate' to upgrade",
            )
    except Exception as exc:
        safe_err = redact(str(exc))
        hint = "Start local database with 'uv run farm db up' or check FARM_DB_URL"
        return CheckResult(
            name="db_and_migrations",
            status="FAIL",
            detail=f"Database unreachable: {safe_err}",
            fix_hint=hint,
        )


async def check_bifrost_health(
    bifrost_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> CheckResult:
    """Check if the local Bifrost LLM gateway is responsive."""
    url = (bifrost_url or os.environ.get("BIFROST_URL") or DEFAULT_BIFROST_URL).rstrip("/")

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=3.0)
    try:
        # Try /health endpoint, then fallback to /
        try:
            resp = await http_client.get(f"{url}/health")
        except httpx.HTTPError:
            resp = await http_client.get(f"{url}/")

        if resp.status_code < 500:
            return CheckResult(
                name="bifrost",
                status="PASS",
                detail=f"Bifrost gateway reachable at {url} (HTTP {resp.status_code})",
            )
        else:
            return CheckResult(
                name="bifrost",
                status="WARN",
                detail=f"Bifrost returned HTTP {resp.status_code} at {url}",
                fix_hint="Check Bifrost logs in D:\\dev-cache\\bifrost\\server.log",
            )
    except Exception as exc:
        return CheckResult(
            name="bifrost",
            status="FAIL",
            detail=f"Bifrost unreachable at {url}: {exc.__class__.__name__}",
            fix_hint="Run 'powershell -File scripts/start-bifrost.ps1' to start Bifrost",
        )
    finally:
        if owns_client:
            await http_client.aclose()


async def check_provider_pools(pool: DbPool) -> CheckResult:
    """Check that each enabled provider pool has at least 1 active connection."""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select p.id, count(c.id) filter (where c.status = 'active') as active_count "
                "from public.providers p "
                "left join public.connections c on p.id = c.provider_id "
                "where p.enabled = true "
                "group by p.id order by p.id"
            )
            rows = await cur.fetchall()

        if not rows:
            return CheckResult(
                name="provider_pools",
                status="WARN",
                detail="No enabled providers configured in the database",
                fix_hint="Sync provider registry or add connections",
            )

        empty_pools: list[str] = []
        pool_summaries: list[str] = []
        for pid, active_cnt in rows:
            pool_summaries.append(f"{pid}({active_cnt})")
            if active_cnt == 0:
                empty_pools.append(str(pid))

        if empty_pools:
            return CheckResult(
                name="provider_pools",
                status="WARN",
                detail=f"Pools without active connections: {', '.join(empty_pools)}",
                fix_hint="Activate or configure connections for empty pools",
            )

        return CheckResult(
            name="provider_pools",
            status="PASS",
            detail=f"All {len(rows)} pool(s) have active connections: {', '.join(pool_summaries)}",
        )
    except Exception as exc:
        return CheckResult(
            name="provider_pools",
            status="FAIL",
            detail=f"Failed to query provider pools: {exc}",
            fix_hint="Ensure database is accessible",
        )


async def check_needs_login(pool: DbPool) -> CheckResult:
    """Check for connections flagged as 'needs_login'."""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select id, provider_id from public.connections "
                "where status = 'needs_login' order by id"
            )
            rows = await cur.fetchall()

        if not rows:
            return CheckResult(
                name="needs_login",
                status="PASS",
                detail="0 connections need login",
            )

        items = [f"{r[0]} ({r[1]})" for r in rows]
        return CheckResult(
            name="needs_login",
            status="WARN",
            detail=f"{len(rows)} connection(s) require login: {', '.join(items)}",
            fix_hint="Run 'farm ai login <conn>' or 'farm mcp login <conn>'",
        )
    except Exception as exc:
        return CheckResult(
            name="needs_login",
            status="FAIL",
            detail=f"Failed to query needs_login connections: {exc}",
        )


async def check_circuits(pool: DbPool) -> CheckResult:
    """Check for connections with open or half-open circuit breakers."""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select connection_id, circuit, consecutive_failures, last_error, cooldown_until "
                "from public.connection_health "
                "where circuit in ('open', 'half_open') order by connection_id"
            )
            rows = await cur.fetchall()

        if not rows:
            return CheckResult(
                name="open_circuits",
                status="PASS",
                detail="0 open circuits",
            )

        details = [f"{r[0]}[{r[1]}, failures={r[2]}]" for r in rows]
        return CheckResult(
            name="open_circuits",
            status="WARN",
            detail=f"{len(rows)} circuit(s) open/half-open: {', '.join(details)}",
            fix_hint="Check provider errors or wait for cooldown to expire",
        )
    except Exception as exc:
        return CheckResult(
            name="open_circuits",
            status="FAIL",
            detail=f"Failed to query circuits: {exc}",
        )


async def check_exhausted_accounts(pool: DbPool) -> CheckResult:
    """Check for exhausted connections and report their reset times."""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select c.id, u.unit, u.next_reset_at "
                "from public.connections c "
                "left join public.consumption_units u on c.id = u.connection_id "
                "where c.status = 'exhausted' order by c.id"
            )
            rows = await cur.fetchall()

        if not rows:
            return CheckResult(
                name="exhausted_accounts",
                status="PASS",
                detail="0 exhausted accounts",
            )

        items: list[str] = []
        for cid, unit, reset_at in rows:
            reset_str = reset_at.isoformat() if reset_at else "unknown"
            unit_str = f" ({unit})" if unit else ""
            items.append(f"{cid}{unit_str} resets {reset_str}")

        return CheckResult(
            name="exhausted_accounts",
            status="WARN",
            detail=f"{len(rows)} exhausted account(s): {', '.join(items)}",
            fix_hint="Wait for quota reset or top up credits",
        )
    except Exception as exc:
        return CheckResult(
            name="exhausted_accounts",
            status="FAIL",
            detail=f"Failed to query exhausted accounts: {exc}",
        )


def check_disk_space(
    min_free_bytes: int = 2 * (1024**3),  # 2 GB
    drives: list[str] | None = None,
) -> CheckResult:
    """Check that free disk space is >= 2 GB on required drives (C: and D: on Windows)."""
    if drives is None:
        if sys.platform == "win32":
            drives = ["C:\\"]
            if os.path.exists("D:\\"):
                drives.append("D:\\")
        else:
            drives = ["/"]

    low_drives: list[str] = []
    summaries: list[str] = []

    for d in drives:
        try:
            usage = shutil.disk_usage(d)
            free_gb = usage.free / (1024**3)
            summaries.append(f"{d} {free_gb:.1f} GB")
            if usage.free < min_free_bytes:
                low_drives.append(f"{d} ({free_gb:.2f} GB < 2.0 GB)")
        except Exception as exc:
            log.warning("doctor.disk_usage_error", drive=d, error=str(exc))

    if low_drives:
        return CheckResult(
            name="disk_space",
            status="FAIL",
            detail=f"Low disk space on: {', '.join(low_drives)}",
            fix_hint="Free disk space to maintain at least 2 GB free",
        )

    return CheckResult(
        name="disk_space",
        status="PASS",
        detail=f"Free disk space ok: {', '.join(summaries)} (threshold >= 2 GB)",
    )


def check_data_dir_writable(data_directory: Path | None = None) -> CheckResult:
    """Check that FARM_DATA_DIR exists and is writable."""
    base = data_directory or get_data_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / f".doctor_probe_{os.getpid()}.tmp"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        return CheckResult(
            name="data_dir_writable",
            status="PASS",
            detail=f"{base} is writable",
        )
    except Exception as exc:
        return CheckResult(
            name="data_dir_writable",
            status="FAIL",
            detail=f"Directory {base} is not writable: {exc}",
            fix_hint=f"Ensure write permissions on {base}",
        )


def check_evidence_dir(data_directory: Path | None = None) -> CheckResult:
    """Calculate and report the size of FARM_DATA_DIR/evidence."""
    base = data_directory or get_data_dir()
    ev_dir = base / "evidence"

    if not ev_dir.is_dir():
        return CheckResult(
            name="evidence_dir_size",
            status="PASS",
            detail=f"Evidence dir empty/not created (0 B in {ev_dir})",
        )

    total_bytes = 0
    count = 0
    try:
        for root, _, files in os.walk(ev_dir):
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                    count += 1
                except OSError:
                    pass
        size_mb = total_bytes / (1024 * 1024)
        return CheckResult(
            name="evidence_dir_size",
            status="PASS",
            detail=f"{size_mb:.2f} MB ({count} files) in {ev_dir}",
        )
    except Exception as exc:
        return CheckResult(
            name="evidence_dir_size",
            status="WARN",
            detail=f"Could not calculate evidence dir size: {exc}",
        )


def check_ai_clis(clis: list[str] | None = None) -> CheckResult:
    """Check that required AI CLIs are found on PATH and report their versions."""
    target_clis = clis or ["claude", "codex", "agy", "hermes"]
    found: list[str] = []
    missing: list[str] = []

    for name in target_clis:
        bin_path = shutil.which(name)
        if not bin_path:
            missing.append(name)
            continue

        ver_str = "found"
        try:
            # Run synchronously via subprocess with short timeout
            # (threads/direct subprocess avoids SelectorEventLoop issues on Windows)
            res = subprocess.run(
                [bin_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
                env=build_child_env(),
            )
            out = (res.stdout.strip() or res.stderr.strip()).splitlines()
            if out:
                # First non-empty line
                first = next((line.strip() for line in out if line.strip()), "version ok")
                ver_str = first[:50]
        except Exception:
            pass
        found.append(f"{name}: {ver_str}")

    if missing:
        status: Literal["PASS", "WARN", "FAIL"] = "WARN" if found else "FAIL"
        return CheckResult(
            name="ai_clis",
            status=status,
            detail=f"Found: [{', '.join(found)}]; Missing on PATH: [{', '.join(missing)}]",
            fix_hint="Install missing CLIs or ensure their directories are in PATH",
        )

    return CheckResult(
        name="ai_clis",
        status="PASS",
        detail=", ".join(found),
    )


# --- Composite Runner ---


async def run_doctor(
    pool: DbPool | None = None,
    data_directory: Path | None = None,
    bifrost_url: str | None = None,
) -> list[CheckResult]:
    """Execute all doctor checks and return the list of CheckResults."""
    from farm.control.backup import check_last_backup
    from farm.control.heartbeat import check_heartbeat
    from farm.control.keepawake import check_keepawake

    data_dir = data_directory or get_data_dir()
    results: list[CheckResult] = []

    # 1. DB and migrations
    db_res = check_db_and_migrations(pool=pool)
    results.append(db_res)

    # 2. Bifrost health
    bifrost_res = await check_bifrost_health(bifrost_url=bifrost_url)
    results.append(bifrost_res)

    # 3-6. DB-dependent checks (pools, needs_login, circuits, exhausted)
    if pool is not None and db_res.status != "FAIL":
        results.append(await check_provider_pools(pool))
        results.append(await check_needs_login(pool))
        results.append(await check_circuits(pool))
        results.append(await check_exhausted_accounts(pool))
    else:
        # If no pool was passed but DB is ok, attempt quick connection
        if db_res.status != "FAIL":
            try:
                from farm.db.pool import open_pool

                async with await open_pool() as quick_pool:
                    results.append(await check_provider_pools(quick_pool))
                    results.append(await check_needs_login(quick_pool))
                    results.append(await check_circuits(quick_pool))
                    results.append(await check_exhausted_accounts(quick_pool))
            except Exception as exc:
                err = f"DB check skipped: {exc}"
                results.append(CheckResult("provider_pools", "WARN", err))
                results.append(CheckResult("needs_login", "WARN", err))
                results.append(CheckResult("open_circuits", "WARN", err))
                results.append(CheckResult("exhausted_accounts", "WARN", err))
        else:
            results.append(CheckResult("provider_pools", "WARN", "DB unavailable"))
            results.append(CheckResult("needs_login", "WARN", "DB unavailable"))
            results.append(CheckResult("open_circuits", "WARN", "DB unavailable"))
            results.append(CheckResult("exhausted_accounts", "WARN", "DB unavailable"))

    # 7. Disk space (>= 2 GB on C: and D:)
    results.append(check_disk_space())

    # 8. FARM_DATA_DIR writable
    results.append(check_data_dir_writable(data_directory=data_dir))

    # 9. Evidence directory size
    results.append(check_evidence_dir(data_directory=data_dir))

    # 10. Last backup age (< 26 h)
    results.append(check_last_backup(backup_directory=data_dir / "backups"))

    # 11. Keep-awake active
    results.append(check_keepawake(data_directory=data_dir))

    # 12. Command consumer heartbeat (< 60 s)
    results.append(check_heartbeat("command_consumer", max_age_s=60.0, data_directory=data_dir))

    # 13. AI CLIs on PATH with versions
    results.append(check_ai_clis())

    return results


def format_doctor_report(results: list[CheckResult]) -> str:
    """Format doctor results into standard PASS/WARN/FAIL output lines."""
    lines = [res.format_line() for res in results]
    pass_cnt = sum(1 for r in results if r.status == "PASS")
    warn_cnt = sum(1 for r in results if r.status == "WARN")
    fail_cnt = sum(1 for r in results if r.status == "FAIL")
    lines.append(f"\nResult: {pass_cnt} passed, {warn_cnt} warnings, {fail_cnt} failed")
    return "\n".join(lines)


def doctor_exit_code(results: list[CheckResult]) -> int:
    """Return 0 if no FAIL checks, 1 if any FAIL check."""
    return 1 if any(r.status == "FAIL" for r in results) else 0


async def main() -> None:
    results = await run_doctor()
    print(format_doctor_report(results))
    sys.exit(doctor_exit_code(results))


if __name__ == "__main__":
    from farm.db.pool import run as run_async

    run_async(main())
