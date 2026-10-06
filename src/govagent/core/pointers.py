"""RFC 6901 JSON pointers: parsing, formatting and segment-aware containment."""

from collections.abc import Sequence

from govagent.domain.errors import PointerError


def parse_pointer(pointer: str) -> list[str]:
    """`/paths/~1pets/get` -> `["paths", "/pets", "get"]`. The root pointer is `""`."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PointerError(f"JSON pointer must be empty or start with '/': {pointer!r}")
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/")]


def format_pointer(segments: Sequence[str | int]) -> str:
    """`["paths", "/pets", "get"]` -> `/paths/~1pets/get`. `~` is escaped before `/`."""
    return "".join("/" + str(s).replace("~", "~0").replace("/", "~1") for s in segments)


def is_within(pointer: str, scope: str) -> bool:
    """True if `pointer` is `scope` itself or one of its descendants (segment-wise)."""
    scope_segments = parse_pointer(scope)
    return parse_pointer(pointer)[: len(scope_segments)] == scope_segments


def normalize_scope(scope: str) -> str:
    """Rule metadata writes the document root as `/`; in RFC 6901 the root is `""`."""
    return "" if scope == "/" else scope
