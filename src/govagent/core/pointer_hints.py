"""Turn a wrong JSON pointer from the model into a precise retry hint.

Models mostly fail on RFC 6901 escaping, not on meaning: they write
`/paths/~1tickets~1{ticketId}~1delete` (the HTTP method glued into the path key) instead of
`/paths/~1tickets~1{ticketId}/delete`, or add a stray `/` after a key ending in `~1`.
Ignoring escaping, both pointers describe the same "flat" path. So a pointer that does not
resolve is compared with the existing nodes on its flat form: a match gives the correctly
escaped pointer to suggest; otherwise the deepest existing node on that path is reported.
"""

from collections.abc import Iterator, Sequence
from typing import Any, cast

from govagent.core.pointers import format_pointer, parse_pointer
from govagent.core.spec_io import resolve
from govagent.domain.errors import PointerError
from govagent.domain.models import PatchOp


def pointer_hints(root: Any, ops: Sequence[PatchOp]) -> list[str]:
    """One hint per pointer of `ops` that does not resolve (and is not a legitimate new key)."""
    flat_index = _flat_index(root)
    hints: list[str] = []
    for op in ops:
        for field, pointer in (("path", op.path), ("from", op.from_)):
            if pointer is None or _resolves(root, pointer):
                continue
            if field == "path" and op.op in ("add", "move") and _parent_resolves(root, pointer):
                continue  # creating a new key under an existing node is fine
            hint = _hint(root, flat_index, pointer)
            if hint is not None:
                hints.append(f"{op.op} {field} {pointer}: {hint}")
    return hints


def correct_pointer(root: Any, pointer: str) -> str | None:
    """The correctly escaped pointer the model probably meant, or None."""
    return _correction(_flat_index(root), pointer)


def _hint(root: Any, flat_index: dict[str, str], pointer: str) -> str | None:
    corrected = _correction(flat_index, pointer)
    if corrected is not None:
        return f"did you mean {corrected} ? (escape only a '/' inside a key, as ~1)"
    try:
        segments = parse_pointer(pointer)
    except PointerError:
        return None
    for depth in range(len(segments) - 1, 0, -1):
        prefix = format_pointer(segments[:depth])
        if _resolves(root, prefix):
            rest = format_pointer(segments[depth:])
            return f"{prefix} exists, but {rest!r} below it does not"
    return None


def _correction(flat_index: dict[str, str], pointer: str) -> str | None:
    """Longest prefix of `pointer` whose flat form names an existing node, re-escaped."""
    try:
        segments = parse_pointer(pointer)
    except PointerError:
        return None
    for depth in range(len(segments), 0, -1):
        existing = flat_index.get(_flat(segments[:depth]))
        if existing is None:
            continue
        corrected = existing + format_pointer(segments[depth:])
        return corrected if corrected != pointer else None
    return None


def _flat(segments: Sequence[str]) -> str:
    """Pointer without escaping: `["paths", "/a/{id}", "get"]` -> `paths//a/{id}/get`."""
    return "/".join(segments)


def _flat_index(root: Any) -> dict[str, str]:
    """flat form -> pointer, for every node. Ambiguous flat forms are left out."""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for segments in _walk(root, []):
        flat = _flat(segments)
        if flat in index and index[flat] != format_pointer(segments):
            ambiguous.add(flat)
        index[flat] = format_pointer(segments)
    return {flat: pointer for flat, pointer in index.items() if flat not in ambiguous}


def _walk(node: Any, path: list[str]) -> Iterator[list[str]]:
    if path:
        yield path
    if isinstance(node, dict):
        for key, value in cast(dict[Any, Any], node).items():
            yield from _walk(value, [*path, str(key)])
    elif isinstance(node, list):
        for index, value in enumerate(cast(list[Any], node)):
            yield from _walk(value, [*path, str(index)])


def _resolves(root: Any, pointer: str) -> bool:
    try:
        resolve(root, pointer)
    except PointerError:
        return False
    return True


def _parent_resolves(root: Any, pointer: str) -> bool:
    try:
        segments = parse_pointer(pointer)
    except PointerError:
        return False
    return bool(segments) and _resolves(root, format_pointer(segments[:-1]))
