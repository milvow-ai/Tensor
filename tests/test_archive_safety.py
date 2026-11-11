import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[1] / "scripts/linux/extract-state.py"
spec = importlib.util.spec_from_file_location("extract_state", script)
assert spec and spec.loader
extract_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract_state)


@pytest.mark.parametrize("kind", ["parent", "symlink", "hardlink", "device"])
def test_archive_cannot_write_outside_restore_root(tmp_path: Path, kind: str) -> None:
    archive = tmp_path / "state.tar"
    entry = tarfile.TarInfo("../escaped" if kind == "parent" else "entry")
    if kind in ("symlink", "hardlink"):
        entry.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        entry.linkname = "../../escaped"
    if kind == "device":
        entry.type = tarfile.FIFOTYPE
    with tarfile.open(archive, "w") as out:
        if kind == "parent":
            entry.size = 4
            out.addfile(entry, io.BytesIO(b"data"))
        else:
            out.addfile(entry)
    with pytest.raises(tarfile.FilterError):
        extract_state.extract(archive, tmp_path / "restore")
    assert not (tmp_path / "escaped").exists()


def test_plain_private_state_roundtrips(tmp_path: Path) -> None:
    archive = tmp_path / "state.tar"
    with tarfile.open(archive, "w") as out:
        entry = tarfile.TarInfo("secrets.env")
        entry.size = 7
        entry.mode = 0o600
        out.addfile(entry, io.BytesIO(b"private"))
    extract_state.extract(archive, tmp_path / "restore")
    assert (tmp_path / "restore/secrets.env").read_bytes() == b"private"
