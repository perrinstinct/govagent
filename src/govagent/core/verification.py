"""Decide whether a fix worked by comparing violation sets before and after (by fingerprint).

Known limitation: fingerprints (`rule_id:pointer`) can shift after array removals or key moves,
so a moved violation may look both resolved and introduced (docs/SPEC.md §4).
"""

from collections.abc import Collection, Sequence

from pydantic import BaseModel

from govagent.domain.models import Severity, Violation

BLOCKING = frozenset({Severity.ERROR, Severity.WARN})


class Verification(BaseModel, frozen=True):
    resolved: bool
    remaining: list[Violation]  # targets still present after the fix
    introduced: list[Violation]  # violations absent before the fix, any severity


def verify(
    before: Sequence[Violation], after: Sequence[Violation], targets: Collection[str]
) -> Verification:
    """`resolved` = every target fingerprint is gone AND no new error/warn was introduced."""
    before_fps = {v.fingerprint for v in before}
    remaining = [v for v in after if v.fingerprint in targets]
    introduced = [v for v in after if v.fingerprint not in before_fps]
    resolved = not remaining and not any(v.severity in BLOCKING for v in introduced)
    return Verification(resolved=resolved, remaining=remaining, introduced=introduced)
