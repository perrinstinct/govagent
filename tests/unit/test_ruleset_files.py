"""Consistency checks between the Spectral ruleset and rules_meta.yaml (no Spectral needed)."""

from typing import Any

import pytest
from ruamel.yaml import YAML

from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.models import FixClass
from tests.conftest import RULE_FIXTURES_DIR, RULESETS_DIR

SCOPE_TEMPLATES = {"operation", "schema"}

SPECTRAL_RULES: dict[str, Any] = YAML(typ="safe").load(  # pyright: ignore[reportUnknownMemberType]
    RULESETS_DIR / "governance.spectral.yaml"
)["rules"]
RULES_META = parse_rules_meta((RULESETS_DIR / "rules_meta.yaml").read_text())


def test_every_spectral_rule_has_metadata_and_vice_versa() -> None:
    assert set(SPECTRAL_RULES) == set(RULES_META)


def test_ruleset_covers_the_thirteen_spec_rules() -> None:
    assert len(SPECTRAL_RULES) == 13


@pytest.mark.parametrize("rule_id", sorted(SPECTRAL_RULES))
def test_spectral_rule_shape(rule_id: str) -> None:
    rule = SPECTRAL_RULES[rule_id]

    assert rule_id.startswith("gov-")
    assert rule["severity"] in {"error", "warn"}  # only blocking severities are verifiable
    assert rule["description"]


@pytest.mark.parametrize("rule_id", sorted(RULES_META))
def test_rule_meta_shape(rule_id: str) -> None:
    meta = RULES_META[rule_id]

    assert meta.scope.startswith("/") or meta.scope in SCOPE_TEMPLATES
    assert all(scope.startswith("/") for scope in meta.extra_write_scopes)
    assert meta.guidance.strip()
    if meta.fix_class is FixClass.NEEDS_HUMAN:
        assert not meta.extra_write_scopes


@pytest.mark.parametrize("rule_id", sorted(SPECTRAL_RULES))
def test_every_rule_has_pass_and_fail_fixtures(rule_id: str) -> None:
    assert (RULE_FIXTURES_DIR / rule_id / "pass.yaml").is_file()
    assert (RULE_FIXTURES_DIR / rule_id / "fail.yaml").is_file()
