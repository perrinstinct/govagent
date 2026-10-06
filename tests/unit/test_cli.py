from pathlib import Path

import pytest
from typer.testing import CliRunner

from govagent.interfaces.cli import app

runner = CliRunner()
SPEC = Path(__file__).resolve().parents[1] / "fixtures" / "specs" / "pets.yaml"


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_fix_refuses_to_run_without_model_config(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean_env.chdir(tmp_path)  # no .env file here

    result = runner.invoke(app, ["fix", str(SPEC)])

    assert result.exit_code == 1
    assert "GOVAGENT_MODEL_ID" in result.stderr
    assert "GOVAGENT_PRICE_INPUT_PER_MTOK" in result.stderr
