from govagent.core.verification import follow_moves, verify
from govagent.domain.models import PatchOp, Severity, Violation


def v(rule_id: str, pointer: str, severity: Severity = Severity.WARN) -> Violation:
    return Violation(rule_id=rule_id, severity=severity, pointer=pointer, message="m")


TARGET = v("summary", "/paths/~1pets/post")
OTHER = v("contact", "/info")


def test_resolved_when_targets_gone_and_nothing_introduced() -> None:
    result = verify([TARGET, OTHER], [OTHER], {TARGET.fingerprint})

    assert result.resolved
    assert result.remaining == []
    assert result.introduced == []


def test_not_resolved_when_a_target_remains() -> None:
    result = verify([TARGET, OTHER], [TARGET, OTHER], {TARGET.fingerprint})

    assert not result.resolved
    assert result.remaining == [TARGET]


def test_not_resolved_when_an_error_or_warning_is_introduced() -> None:
    new = v("https", "/servers/0/url", Severity.ERROR)

    result = verify([TARGET], [new], {TARGET.fingerprint})

    assert not result.resolved
    assert result.introduced == [new]


def test_introduced_info_or_hint_does_not_block() -> None:
    hint = v("style", "/info/title", Severity.HINT)

    result = verify([TARGET], [hint], {TARGET.fingerprint})

    assert result.resolved
    assert result.introduced == [hint]


def test_preexisting_violations_are_not_counted_as_introduced() -> None:
    result = verify([TARGET, OTHER], [OTHER], {TARGET.fingerprint})

    assert OTHER not in result.introduced


def test_moved_preexisting_violation_is_not_counted_as_introduced() -> None:
    rename = PatchOp.model_validate(
        {"op": "move", "from": "/paths/~1petStore", "path": "/paths/~1pet-store"}
    )
    kebab = v("kebab", "/paths/~1petStore", Severity.ERROR)
    summary_before = v("summary", "/paths/~1petStore/get")
    summary_after = v("summary", "/paths/~1pet-store/get")

    result = verify([kebab, summary_before], [summary_after], {kebab.fingerprint}, [rename])

    assert result.resolved
    assert result.introduced == []


def test_follow_moves_applies_moves_in_order_and_only_under_the_source() -> None:
    ops = [
        PatchOp.model_validate({"op": "move", "from": "/a/x", "path": "/a/y"}),
        PatchOp.model_validate({"op": "move", "from": "/a/y", "path": "/b/z"}),
    ]
    moved = follow_moves([v("r", "/a/x/deep"), v("r", "/a/xx"), v("r", "/c")], ops)

    assert [m.pointer for m in moved] == ["/b/z/deep", "/a/xx", "/c"]
