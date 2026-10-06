"""Deterministic check that a fix only writes where its rule allows (CLAUDE.md rule 2)."""

from collections.abc import Sequence

from govagent.core.pointers import is_within, normalize_scope
from govagent.domain.errors import PointerError
from govagent.domain.models import PatchOp


def scope_violations(
    ops: Sequence[PatchOp], scope_pointer: str, extra_write_scopes: Sequence[str] = ()
) -> list[str]:
    """Return one message per offending pointer; an empty list means every op is allowed.

    An op is allowed if its `path` (and `from` for a move) equals or descends from the group's
    scope pointer or one of the rule's extra write scopes. The document root is never writable.
    """
    allowed = [normalize_scope(scope) for scope in (scope_pointer, *extra_write_scopes)]
    problems: list[str] = []
    for index, op in enumerate(ops):
        for field, pointer in (("path", op.path), ("from", op.from_)):
            if pointer is None:
                continue
            if pointer == "":
                problems.append(f"op #{index}: {op.op} on the document root is not allowed")
                continue
            try:
                inside = any(is_within(pointer, scope) for scope in allowed)
            except PointerError as exc:
                problems.append(f"op #{index}: invalid {field} {pointer!r}: {exc}")
                continue
            if not inside:
                problems.append(
                    f"op #{index}: {op.op} {field} {pointer} is outside the allowed scopes "
                    f"({', '.join(scope or '/' for scope in allowed)})"
                )
    return problems
