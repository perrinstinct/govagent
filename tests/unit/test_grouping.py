import pytest

from govagent.core.grouping import group_id, group_violations, needs_human, resolve_scope
from govagent.domain.errors import GroupingError
from govagent.domain.models import FixClass, RuleMeta, Severity, Violation


def meta(rule_id: str, scope: str, fix_class: FixClass = FixClass.AUTO) -> RuleMeta:
    return RuleMeta(rule_id=rule_id, fix_class=fix_class, breaking=False, scope=scope, guidance="g")


def violation(rule_id: str, pointer: str) -> Violation:
    return Violation(rule_id=rule_id, severity=Severity.WARN, pointer=pointer, message="m")


@pytest.mark.parametrize(
    ("scope", "pointer", "expected"),
    [
        ("/servers", "/servers/1/url", "/servers"),
        ("/", "", ""),
        ("operation", "/paths/~1pets/get", "/paths/~1pets/get"),
        ("operation", "/paths/~1pets/get/responses/500/content", "/paths/~1pets/get"),
        ("operation", "/components/responses/NotFound/content", "/components/responses/NotFound"),
        ("schema", "/components/schemas/Pet/properties/pet_name", "/components/schemas/Pet"),
        (
            "schema",
            "/components/schemas/Pet/properties/owner/properties/First",
            "/components/schemas/Pet/properties/owner",
        ),
    ],
)
def test_resolve_scope(scope: str, pointer: str, expected: str) -> None:
    assert resolve_scope(meta("r", scope), pointer) == expected


@pytest.mark.parametrize(
    ("scope", "pointer"),
    [
        ("operation", "/paths/~1pets"),  # path item, not an operation
        ("operation", "/info"),
        ("schema", "/components/schemas/Pet"),
        ("unknown-template", "/info"),
    ],
)
def test_unresolvable_scope_raises(scope: str, pointer: str) -> None:
    with pytest.raises(GroupingError):
        resolve_scope(meta("r", scope), pointer)


def test_groups_by_rule_and_scope_in_order_of_appearance() -> None:
    rules = {"camel": meta("camel", "schema"), "summary": meta("summary", "operation")}
    violations = [
        violation("summary", "/paths/~1b/get"),
        violation("camel", "/components/schemas/Pet/properties/a_b"),
        violation("camel", "/components/schemas/Pet/properties/c_d"),
        violation("summary", "/paths/~1a/get"),
    ]

    groups = group_violations(violations, rules)

    assert [(g.rule_id, g.scope_pointer, len(g.violations)) for g in groups] == [
        ("summary", "/paths/~1b/get", 1),
        ("camel", "/components/schemas/Pet", 2),
        ("summary", "/paths/~1a/get", 1),
    ]


def test_group_ids_are_stable_and_distinct() -> None:
    assert group_id("r", "/a") == group_id("r", "/a")
    assert group_id("r", "/a") != group_id("r", "/b")
    assert group_id("r", "/a") != group_id("s", "/a")


def test_unknown_rule_raises() -> None:
    with pytest.raises(GroupingError, match="no metadata"):
        group_violations([violation("ghost", "/info")], {})


def test_needs_human_follows_rule_metadata() -> None:
    rules = {"contact": meta("contact", "/info", FixClass.NEEDS_HUMAN), "s": meta("s", "/servers")}
    groups = group_violations([violation("contact", "/info"), violation("s", "/servers/0")], rules)

    assert [needs_human(g, rules) for g in groups] == [True, False]
