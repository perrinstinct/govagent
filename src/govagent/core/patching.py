"""JSON Patch (RFC 6902 subset: add, remove, replace, move) applied directly on ruamel nodes.

Working on the round-trip nodes instead of plain dicts is what keeps comments, key order and
quoting intact. A few additions keep diffs minimal:
- a `move` inside the same mapping (a rename) keeps the key's position and its comment;
- an `add` of a well-known OpenAPI key is inserted at its conventional position
  (e.g. `summary` before `responses`) instead of at the end of the mapping;
- replacing a scalar keeps its quote style and the gap before its end-of-line comment.
"""

import copy
import re
from typing import Any, cast

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

from govagent.core.openapi import HTTP_METHODS
from govagent.core.pointers import format_pointer, parse_pointer
from govagent.domain.errors import PatchError
from govagent.domain.models import PatchOp

# Conventional key order, by the kind of object being patched.
_ROOT_ORDER = (
    "openapi", "info", "jsonSchemaDialect", "servers", "security", "tags", "externalDocs",
    "paths", "webhooks", "components",
)  # fmt: skip
_INFO_ORDER = (
    "title", "summary", "description", "termsOfService", "contact", "license", "version",
)  # fmt: skip
_OPERATION_ORDER = (
    "tags", "summary", "description", "externalDocs", "operationId", "parameters",
    "requestBody", "responses", "callbacks", "deprecated", "security", "servers",
)  # fmt: skip
_INDEX = re.compile(r"0|[1-9][0-9]*")


def apply_patch(root: dict[str, Any], ops: list[PatchOp]) -> dict[str, Any]:
    """Apply `ops` in order on a deep copy of `root` and return it. All or nothing."""
    patched = copy.deepcopy(root)
    for index, op in enumerate(ops):
        try:
            _apply_op(patched, op)
        except PatchError as exc:
            raise PatchError(f"op #{index} ({op.op} {op.path}): {exc}") from exc
    return patched


def _apply_op(root: dict[str, Any], op: PatchOp) -> None:
    if op.path == "":
        raise PatchError("operations on the document root are not allowed")
    if op.op in ("add", "replace") and "value" not in op.model_fields_set:
        raise PatchError(f"{op.op} requires a value")

    match op.op:
        case "add":
            _add(root, parse_pointer(op.path), op.value)
        case "remove":
            _remove(root, parse_pointer(op.path))
        case "replace":
            _replace(root, parse_pointer(op.path), op.value)
        case "move":
            _move(root, op)


def _parent(root: dict[str, Any], segments: list[str]) -> tuple[Any, str]:
    node: Any = root
    for depth, segment in enumerate(segments[:-1]):
        if isinstance(node, dict) and segment in node:
            node = cast(dict[str, Any], node)[segment]
        elif isinstance(node, list) and _valid_index(segment, len(cast(list[Any], node))):
            node = cast(list[Any], node)[int(segment)]
        else:
            raise PatchError(f"{format_pointer(segments[: depth + 1])} does not exist")
    return node, segments[-1]


def _valid_index(token: str, upper: int) -> bool:
    return _INDEX.fullmatch(token) is not None and int(token) < upper


def _add(root: dict[str, Any], segments: list[str], value: Any) -> None:
    parent, key = _parent(root, segments)
    if isinstance(parent, dict):
        mapping = cast(dict[str, Any], parent)
        if key in mapping:
            _assign(mapping, key, value)
        else:
            _insert_key(mapping, key, value, segments[:-1])
    elif isinstance(parent, list):
        items = cast(list[Any], parent)
        if key == "-":
            items.append(value)
        elif _valid_index(key, len(items) + 1):
            items.insert(int(key), value)
        else:
            raise PatchError(f"invalid array index {key!r}")
    else:
        raise PatchError("parent is not a container")


def _remove(root: dict[str, Any], segments: list[str]) -> Any:
    parent, key = _parent(root, segments)
    if isinstance(parent, dict) and key in parent:
        return cast(dict[str, Any], parent).pop(key)
    if isinstance(parent, list) and _valid_index(key, len(cast(list[Any], parent))):
        return cast(list[Any], parent).pop(int(key))
    raise PatchError("target does not exist")


def _replace(root: dict[str, Any], segments: list[str], value: Any) -> None:
    parent, key = _parent(root, segments)
    if isinstance(parent, dict) and key in parent:
        _assign(cast(dict[str, Any], parent), key, value)
    elif isinstance(parent, list) and _valid_index(key, len(cast(list[Any], parent))):
        _assign(cast(list[Any], parent), int(key), value)
    else:
        raise PatchError("target does not exist")


def _assign(container: dict[str, Any] | list[Any], key: Any, value: Any) -> None:
    """Overwrite an existing entry, keeping quote style and comment spacing for scalars."""
    old = container[key]
    if type(value) is str and isinstance(old, SingleQuotedScalarString | DoubleQuotedScalarString):
        value = type(old)(value)
    container[key] = value
    if _is_scalar(old) and _is_scalar(value):
        _realign_comment(container, key, old, value)


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, dict | list)


def _rendered_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return len(str(value).lower())
    quotes = 2 if isinstance(value, SingleQuotedScalarString | DoubleQuotedScalarString) else 0
    return len(str(value)) + quotes


def _realign_comment(container: Any, key: Any, old: Any, new: Any) -> None:
    """ruamel pins end-of-line comments to their original column; keep the original gap."""
    if isinstance(container, CommentedMap):
        slot, value_col_index = 2, 3  # ca.items[key][2]; lc.data[key] = [kl, kc, vl, vc]
    elif isinstance(container, CommentedSeq):
        slot, value_col_index = 0, 1  # ca.items[idx][0]; lc.data[idx] = [line, col]
    else:
        return
    entries = cast(dict[Any, Any], container.ca.items)  # pyright: ignore[reportUnknownMemberType]
    positions = cast(dict[Any, list[int]], container.lc.data)  # pyright: ignore[reportUnknownMemberType]
    comments = cast(list[Any] | None, entries.get(key))
    if not comments or comments[slot] is None or key not in positions:
        return
    token = comments[slot]
    value_col = positions[key][value_col_index]
    gap = max(1, int(token.column) - value_col - _rendered_len(old))
    token.column = value_col + _rendered_len(new) + gap


def _move(root: dict[str, Any], op: PatchOp) -> None:
    if op.from_ is None:
        raise PatchError("move requires 'from'")
    source, target = parse_pointer(op.from_), parse_pointer(op.path)
    if source == target:
        return
    if not source:
        raise PatchError("cannot move the document root")
    if target[: len(source)] == source:
        raise PatchError("cannot move a node into one of its descendants")

    source_parent, source_key = _parent(root, source)
    if (
        isinstance(source_parent, CommentedMap)
        and source_key in source_parent
        and source[:-1] == target[:-1]
        and target[-1] not in source_parent
    ):
        _rename_key(source_parent, source_key, target[-1])
        return
    _add(root, target, _remove(root, source))


def _rename_key(mapping: CommentedMap, old: str, new: str) -> None:
    """Rename in place: same position, comment carried over."""
    items = cast(dict[str, Any], mapping)
    comments = cast(dict[str, Any], mapping.ca.items)  # pyright: ignore[reportUnknownMemberType]
    position = list(items).index(old)
    comment = comments.pop(old, None)
    value = items.pop(old)
    _insert_at(mapping, position, new, value)
    if comment is not None:
        comments[new] = comment


def _insert_key(mapping: dict[str, Any], key: str, value: Any, parent: list[str]) -> None:
    order = _conventional_order(parent)
    if isinstance(mapping, CommentedMap) and order is not None and key in order:
        rank = order.index(key)
        for position, existing in enumerate(cast(dict[str, Any], mapping)):
            if existing in order and order.index(existing) > rank:
                _insert_at(mapping, position, key, value)
                return
    mapping[key] = value


def _insert_at(mapping: CommentedMap, position: int, key: str, value: Any) -> None:
    mapping.insert(position, key, value)  # pyright: ignore[reportUnknownMemberType]


def _conventional_order(parent: list[str]) -> tuple[str, ...] | None:
    if not parent:
        return _ROOT_ORDER
    if parent == ["info"]:
        return _INFO_ORDER
    if len(parent) == 3 and parent[0] == "paths" and parent[2] in HTTP_METHODS:
        return _OPERATION_ORDER
    return None
