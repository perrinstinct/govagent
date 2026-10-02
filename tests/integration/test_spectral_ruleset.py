"""Runs the real Spectral CLI against the per-rule fixtures."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import RULE_FIXTURES_DIR, RULESETS_DIR

pytestmark = pytest.mark.integration

RULESET = RULESETS_DIR / "governance.spectral.yaml"
RULE_IDS = sorted(path.name for path in RULE_FIXTURES_DIR.iterdir() if path.is_dir())


def lint_codes(spec: Path) -> list[str]:
    spectral = shutil.which("spectral")
    if spectral is None:
        pytest.fail("spectral not found on PATH")
    # Spectral exits non-zero when it finds violations; only empty/invalid stdout is a failure.
    result = subprocess.run(
        [spectral, "lint", "--quiet", "--format", "json", "--ruleset", str(RULESET), str(spec)],
        capture_output=True,
        text=True,
        check=False,
    )
    return [item["code"] for item in json.loads(result.stdout)]


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_pass_fixture_has_no_violation_for_its_rule(rule_id: str) -> None:
    assert rule_id not in lint_codes(RULE_FIXTURES_DIR / rule_id / "pass.yaml")


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_fail_fixture_triggers_its_rule(rule_id: str) -> None:
    assert rule_id in lint_codes(RULE_FIXTURES_DIR / rule_id / "fail.yaml")
