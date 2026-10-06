from govagent.core.verification import verify
from govagent.domain.models import Severity, Violation


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
