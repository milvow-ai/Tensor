from typer.testing import CliRunner

import farm
from farm.control.cli import app

runner = CliRunner()


def test_smoke() -> None:
    assert farm.__version__ == "0.0.1"
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.0.1" in result.stdout
