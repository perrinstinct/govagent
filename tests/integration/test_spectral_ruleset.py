"""Every rule against its fixtures, through the real Spectral adapter.

Contract: `pass.yaml` is a valid spec with no violation at all; `fail.yaml` is a valid spec
that triggers its own rule and nothing else.
"""

import pytest

from govagent.adapters.spectral import SpectralLinter
from govagent.core.spec_io import load_spec
from tests.conftest import RULE_FIXTURES_DIR, RULESETS_DIR

pytestmark = pytest.mark.integration

RULE_IDS = sorted(path.name for path in RULE_FIXTURES_DIR.iterdir() if path.is_dir())
LINTER = SpectralLinter("spectral", RULESETS_DIR / "governance.spectral.yaml")


def fixture(rule_id: str, kind: str) -> str:
    return (RULE_FIXTURES_DIR / rule_id / f"{kind}.yaml").read_text()


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_pass_fixture_is_valid_and_clean(rule_id: str) -> None:
    text = fixture(rule_id, "pass")

    load_spec(text)
    assert LINTER.lint(text) == []


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_fail_fixture_triggers_only_its_rule(rule_id: str) -> None:
    text = fixture(rule_id, "fail")

    load_spec(text)
    assert {v.rule_id for v in LINTER.lint(text)} == {rule_id}


def test_all_thirteen_rules_have_fixtures() -> None:
    assert len(RULE_IDS) == 13


COMMENTED = (RULE_FIXTURES_DIR.parent / "specs" / "commented.yaml").read_text()


def test_commented_spec_is_clean() -> None:
    assert LINTER.lint(COMMENTED) == []


def test_shared_component_violation_is_reported_once_at_its_definition() -> None:
    # The Problem response is $ref'd by two operations (400 and 404).
    text = COMMENTED.replace("application/problem+json", "application/json")

    violations = LINTER.lint(text)

    assert [v.pointer for v in violations] == ["/components/responses/Problem/content"]
