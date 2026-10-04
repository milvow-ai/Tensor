"""Tests for local Postgres fsync durability policy.

Ensures that the runtime database (<FARM_DATA_DIR>/pg) always has fsync = on
to survive power loss, while throwaway test clusters (<FARM_DATA_DIR>/pgtest)
have fsync = off.
"""

import tempfile
from pathlib import Path

from farm.db.local import (
    _CONF_BEGIN,
    _CONF_END,
    _CONF_RE,
    _apply_local_conf,
    get_local_server,
)
from farm.settings import data_dir


def _extract_managed_block(conf_path: Path) -> str:
    text = conf_path.read_text(encoding="utf-8")
    assert _CONF_BEGIN in text, f"Missing {_CONF_BEGIN} in {conf_path}"
    assert _CONF_END in text, f"Missing {_CONF_END} in {conf_path}"
    match = _CONF_RE.search(text)
    assert match is not None, f"Managed block not found in {conf_path}"
    return match.group(0)


def test_runtime_data_dir_fsync_on() -> None:
    """The runtime data dir (<FARM_DATA_DIR>/pg) must have fsync = on."""
    pg_dir = data_dir() / "pg"
    # Ensure server/config is applied
    if pg_dir.exists() and (pg_dir / "postgresql.conf").exists():
        _apply_local_conf(pg_dir)
    else:
        get_local_server(pg_dir)

    conf_path = pg_dir / "postgresql.conf"
    assert conf_path.exists()
    block = _extract_managed_block(conf_path)
    assert "fsync = on" in block
    assert "fsync = off" not in block

    # Passing durable=False to the runtime dir must be ignored ("never for <FARM_DATA_DIR>/pg")
    _apply_local_conf(pg_dir, durable=False)
    block_after = _extract_managed_block(conf_path)
    assert "fsync = on" in block_after
    assert "fsync = off" not in block_after


def test_test_data_dir_fsync_off() -> None:
    """The test data dir (<FARM_DATA_DIR>/pgtest) must have fsync = off."""
    pgtest_dir = data_dir() / "pgtest"
    if pgtest_dir.exists() and (pgtest_dir / "postgresql.conf").exists():
        _apply_local_conf(pgtest_dir)
    else:
        get_local_server(pgtest_dir)

    conf_path = pgtest_dir / "postgresql.conf"
    assert conf_path.exists()
    block = _extract_managed_block(conf_path)
    assert "fsync = off" in block
    assert "fsync = on" not in block


def test_explicit_durable_parameter() -> None:
    """Explicit durable parameter controls fsync in custom data directories."""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td)
        conf_path = target / "postgresql.conf"
        conf_path.write_text("# Initial custom conf\n", encoding="utf-8")

        # Default for non-runtime/throwaway dir is durable=False -> fsync = off
        _apply_local_conf(target)
        block = _extract_managed_block(conf_path)
        assert "fsync = off" in block
        assert "fsync = on" not in block

        # Explicit durable=True -> fsync = on
        _apply_local_conf(target, durable=True)
        block = _extract_managed_block(conf_path)
        assert "fsync = on" in block
        assert "fsync = off" not in block

        # Explicit durable=False -> fsync = off
        _apply_local_conf(target, durable=False)
        block = _extract_managed_block(conf_path)
        assert "fsync = off" in block
        assert "fsync = on" not in block
