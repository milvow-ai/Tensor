"""Tests for account home resolution, staging directories, atomic swaps, and drivers."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from farm.connect.drivers.base import (
    Identity,
    NeedsInput,
    Success,
    Url,
    UserCode,
)
from farm.connect.drivers.fake import FakeDriver
from farm.connect.homes import (
    account_home,
    discard,
    promote,
    staging_home,
)
from farm.settings import data_dir


def test_account_home_resolution(tmp_path: Path) -> None:
    # 1. meta.home takes precedence
    meta_home = str(tmp_path / "custom_home")
    assert account_home({"home": meta_home}, "claude-01") == Path(meta_home)

    # 2. meta.config_dir used when home is absent
    meta_config = str(tmp_path / "custom_config")
    assert account_home({"config_dir": meta_config}, "claude-01") == Path(meta_config)

    # 3. Defaults to <data_dir>/ai/<id>
    expected = data_dir() / "ai" / "claude-01"
    assert account_home({}, "claude-01") == expected
    assert account_home(None, "claude-01") == expected


def test_staging_home_creation(tmp_path: Path) -> None:
    home = tmp_path / "ai_account"
    home.mkdir()
    session_id = uuid4()
    first_8 = str(session_id)[:8]

    staging = staging_home(home, session_id)
    assert staging == tmp_path / f"ai_account.login-{first_8}"
    assert staging.exists()
    assert staging.is_dir()
    assert list(staging.iterdir()) == []

    # Calling staging_home again clears any leftover contents
    (staging / "leftover.txt").write_text("old", encoding="utf-8")
    staging2 = staging_home(home, session_id)
    assert staging2 == staging
    assert list(staging2.iterdir()) == []


def test_promote_swaps_and_keeps_one_prev(tmp_path: Path) -> None:
    home = tmp_path / "acc"
    home.mkdir()
    (home / "version.txt").write_text("v1", encoding="utf-8")

    staging = tmp_path / "acc.login-12345678"
    staging.mkdir()
    (staging / "version.txt").write_text("v2", encoding="utf-8")

    # Promote staging to home
    promote(staging, home)
    assert home.exists()
    assert (home / "version.txt").read_text(encoding="utf-8") == "v2"
    assert not staging.exists()

    prev_dirs = list(tmp_path.glob("acc.prev-*"))
    assert len(prev_dirs) == 1
    assert (prev_dirs[0] / "version.txt").read_text(encoding="utf-8") == "v1"

    # Promote again with a third version
    staging3 = tmp_path / "acc.login-87654321"
    staging3.mkdir()
    (staging3 / "version.txt").write_text("v3", encoding="utf-8")

    promote(staging3, home)
    assert (home / "version.txt").read_text(encoding="utf-8") == "v3"

    # Only the newest prev directory is kept
    prev_dirs2 = sorted(tmp_path.glob("acc.prev-*"))
    assert len(prev_dirs2) == 1
    assert (prev_dirs2[0] / "version.txt").read_text(encoding="utf-8") == "v2"


def test_promote_rollback_when_second_rename_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "acc_rollback"
    home.mkdir()
    (home / "marker.txt").write_text("original", encoding="utf-8")

    staging = tmp_path / "acc_rollback.login-11112222"
    staging.mkdir()
    (staging / "marker.txt").write_text("staged", encoding="utf-8")

    orig_replace = os.replace

    def failing_replace(src: Path | str, dst: Path | str) -> None:
        if Path(src) == staging and Path(dst) == home:
            raise OSError("Simulated second rename disk error")
        orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="Simulated second rename disk error"):
        promote(staging, home)

    # Home should have rolled back to the original content
    assert home.exists()
    assert (home / "marker.txt").read_text(encoding="utf-8") == "original"
    # Staging should still be untouched
    assert staging.exists()
    assert (staging / "marker.txt").read_text(encoding="utf-8") == "staged"


def test_discard_directory_and_file(tmp_path: Path) -> None:
    # Directory
    d = tmp_path / "dir_to_discard"
    d.mkdir()
    (d / "file.txt").write_text("abc", encoding="utf-8")
    discard(d)
    assert not d.exists()
    # Discard twice is fine
    discard(d)

    # File
    f = tmp_path / "file_to_discard.txt"
    f.write_text("xyz", encoding="utf-8")
    discard(f)
    assert not f.exists()
    # Discard twice is fine
    discard(f)


def test_identity_masked_email() -> None:
    ident = Identity(
        provider_account_id="acc-123",
        email="developer@example.com",
        plan="pro",
        org="my-org",
    )
    assert ident.masked_email() == "d\u2022\u2022\u2022\u2022\u2022@example.com"
    assert ident.masked_email() == "d•••••@example.com"

    # Edge cases
    assert Identity("acc-2", "").masked_email() == ""
    assert Identity("acc-3", "plain").masked_email() == "plain"


@pytest.mark.asyncio
async def test_fake_driver(tmp_path: Path) -> None:
    events = [
        Url("https://example.com/auth"),
        UserCode("ABCD-1234", "https://example.com/device"),
        NeedsInput("paste code"),
        Success(),
    ]
    ident = Identity("prov-99", "dev@org.com")
    models = ["model-a", "model-b"]
    driver = FakeDriver(events=events, identity=ident, models=models)

    staging = tmp_path / "staging_home"
    staging.mkdir()
    await driver.start(staging, hint="dev@org.com")

    collected = []
    async for ev in driver.events():
        collected.append(ev)

    assert len(collected) == 4
    # Marker file written on Success
    assert (staging / ".login_success_marker").exists()
    assert (staging / ".login_success_marker").read_text(encoding="utf-8") == "ok"

    await driver.submit("secret-code")
    assert driver.submitted == ["secret-code"]

    await driver.cancel()
    assert driver.cancelled is True

    assert await driver.identity(staging) == ident
    assert await driver.models(staging) == models
