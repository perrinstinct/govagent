"""Group violations by (rule, scope) so that one fix attempt handles a whole scope.

A rule's `scope` in rules_meta.yaml is either a literal pointer (`/servers`, `/` for the root)
or a template resolved from each violation's pointer:
- `operation`: the enclosing operation `/paths/<path>/<method>`; for violations located in a
  shared component (e.g. a `$ref`'d response), the enclosing `/components/<kind>/<name>`;
- `schema`: the schema object owning the offending property (pointer up to the last
  `properties` segment).
"""

import hashlib
from collections.abc import Mapping, Sequence

from govagent.core.openapi import HTTP_METHODS
from govagent.core.pointers import format_pointer, normalize_scope, parse_pointer
from govagent.domain.errors import GroupingError
from govagent.domain.models import FixClass, RuleMeta, Violation, ViolationGroup


def resolve_scope(meta: RuleMeta, pointer: str) -> str:
    if meta.scope.startswith("/"):
        return normalize_scope(meta.scope)
    segments = parse_pointer(pointer)
    if meta.scope == "operation":
        if len(segments) >= 3 and segments[0] == "paths" and segments[2] in HTTP_METHODS:
            return format_pointer(segments[:3])
        if len(segments) >= 3 and segments[0] == "components":
            return format_pointer(segments[:3])
    elif meta.scope == "schema":
        if "properties" in segments:
            last = len(segments) - 1 - segments[::-1].index("properties")
            return format_pointer(segments[:last])
    else:
        raise GroupingError(f"{meta.rule_id}: unknown scope template {meta.scope!r}")
    raise GroupingError(f"{meta.rule_id}: cannot resolve {meta.scope!r} scope for {pointer}")


def group_id(rule_id: str, scope_pointer: str) -> str:
    return hashlib.sha256(f"{rule_id}\n{scope_pointer}".encode()).hexdigest()[:12]


def group_violations(
    violations: Sequence[Violation], rules_meta: Mapping[str, RuleMeta]
) -> list[ViolationGroup]:
    """Groups in order of first appearance; violations keep their linter order."""
    buckets: dict[tuple[str, str], list[Violation]] = {}
    for violation in violations:
        meta = rules_meta.get(violation.rule_id)
        if meta is None:
            raise GroupingError(f"no metadata for rule {violation.rule_id!r}")
        key = (violation.rule_id, resolve_scope(meta, violation.pointer))
        buckets.setdefault(key, []).append(violation)
    return [
        ViolationGroup(
            id=group_id(rule_id, scope), rule_id=rule_id, scope_pointer=scope, violations=items
        )
        for (rule_id, scope), items in buckets.items()
    ]


def needs_human(group: ViolationGroup, rules_meta: Mapping[str, RuleMeta]) -> bool:
    """True when the rule requires business knowledge: no LLM call for this group."""
    return rules_meta[group.rule_id].fix_class is FixClass.NEEDS_HUMAN
