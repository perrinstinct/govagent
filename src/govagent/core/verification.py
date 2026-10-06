"""Decide whether a fix worked by comparing violation sets before and after (by fingerprint).

Fingerprints are `rule_id:pointer`, so a pre-existing violation whose node was moved by the
patch (a rename) would look new. `verify` replays the patch's `move` operations on the
"before" pointers first. Known limitation: shifts caused by array insertions / removals are not
replayed, so such a violation may still look both resolved and introduced (docs/SPEC.md §4).
"""

from collections.abc import Collection, Sequence

from pydantic import BaseModel

from govagent.core.pointers import format_pointer, is_within, parse_pointer
from govagent.domain.models import PatchOp, Severity, Violation

BLOCKING = frozenset({Severity.ERROR, Severity.WARN})


class Verification(BaseModel, frozen=True):
    resolved: bool
    remaining: list[Violation]  # targets still present after the fix
    introduced: list[Violation]  # violations absent before the fix, any severity


def verify(
    before: Sequence[Violation],
    after: Sequence[Violation],
    targets: Collection[str],
    ops: Sequence[PatchOp] = (),
) -> Verification:
    """`resolved` = every target fingerprint is gone AND no new error/warn was introduced."""
    before_fps = {v.fingerprint for v in follow_moves(before, ops)}
    remaining = [v for v in after if v.fingerprint in targets]
    introduced = [v for v in after if v.fingerprint not in before_fps]
    resolved = not remaining and not any(v.severity in BLOCKING for v in introduced)
    return Verification(resolved=resolved, remaining=remaining, introduced=introduced)


def follow_moves(violations: Sequence[Violation], ops: Sequence[PatchOp]) -> list[Violation]:
    """Rewrite pointers located under a `move` source to their new location, op by op."""
    moved = list(violations)
    for op in ops:
        if op.op == "move" and op.from_ is not None:
            moved = [_relocate(v, op.from_, op.path) for v in moved]
    return moved


def _relocate(violation: Violation, source: str, target: str) -> Violation:
    if not is_within(violation.pointer, source):
        return violation
    rest = parse_pointer(violation.pointer)[len(parse_pointer(source)) :]
    new_pointer = format_pointer([*parse_pointer(target), *rest])
    return violation.model_copy(update={"pointer": new_pointer})
