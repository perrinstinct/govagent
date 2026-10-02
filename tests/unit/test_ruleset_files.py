"""Consistency checks between the Spectral ruleset and rules_meta.yaml (no Spectral needed)."""

from typing import Any

import pytest
from ruamel.yaml import YAML

from tests.conftest import RULE_FIXTURES_DIR, RULESETS_DIR

FIX_CLASSES = {"auto", "needs_human_input"}
SCOPE_TEMPLATES = {"operation", "schema"}


def load(name: str) -> dict[str, Any]:
    return YAML(typ="safe").load(RULESETS_DIR / name)


SPECTRAL_RULES: dict[str, Any] = load("governance.spectral.yaml")["rules"]
RULES_META: dict[str, Any] = load("rules_meta.yaml")["rules"]


def test_every_spectral_rule_has_metadata_and_vice_versa() -> None:
    assert set(SPECTRAL_RULES) == set(RULES_META)


@pytest.mark.parametrize("rule_id", sorted(SPECTRAL_RULES))
def test_spectral_rule_shape(rule_id: str) -> None:
    rule = SPECTRAL_RULES[rule_id]

    assert rule_id.startswith("gov-")
    assert rule["severity"] in {"error", "warn", "info", "hint"}
    assert rule["description"]


@pytest.mark.parametrize("rule_id", sorted(RULES_META))
def test_rule_meta_shape(rule_id: str) -> None:
    meta = RULES_META[rule_id]

    assert meta["fix_class"] in FIX_CLASSES
    assert isinstance(meta["breaking"], bool)
    assert meta["scope"].startswith("/") or meta["scope"] in SCOPE_TEMPLATES
    assert all(scope.startswith("/") for scope in meta["extra_write_scopes"])
    assert meta["guidance"].strip()


@pytest.mark.parametrize("rule_id", sorted(SPECTRAL_RULES))
def test_every_rule_has_pass_and_fail_fixtures(rule_id: str) -> None:
    assert (RULE_FIXTURES_DIR / rule_id / "pass.yaml").is_file()
    assert (RULE_FIXTURES_DIR / rule_id / "fail.yaml").is_file()
