"""Parse rulesets/rules_meta.yaml into RuleMeta objects."""

from typing import Any, cast

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from govagent.domain.errors import ConfigError
from govagent.domain.models import RuleMeta


def parse_rules_meta(text: str) -> dict[str, RuleMeta]:
    """`{rule_id: RuleMeta}`. Raises ConfigError on malformed metadata."""
    try:
        data = cast(dict[str, Any], YAML(typ="safe").load(text))  # pyright: ignore[reportUnknownMemberType]
        rules = cast(dict[str, dict[str, Any]], data["rules"])
        return {rule_id: RuleMeta(rule_id=rule_id, **fields) for rule_id, fields in rules.items()}
    except (YAMLError, KeyError, TypeError, ValidationError) as exc:
        raise ConfigError(f"invalid rules metadata: {exc}") from exc
