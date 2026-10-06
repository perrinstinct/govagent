"""Load and dump OpenAPI specs without losing formatting.

YAML goes through ruamel.yaml round-trip mode (comments, key order, quotes and flow style are
kept) with the indentation detected from the source, so an unmodified spec dumps back
byte-identical. Known limitation: explicit `null` / `~` values are written back as empty values.

JSON has no comments: it is loaded with `json.loads` into ordered CommentedMaps (so patching
works the same way) and written back with `json.dumps` using the detected indentation.
"""

import copy
import io
import itertools
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import (
    OpenAPIValidationError,
    ValidatorDetectError,
)
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from govagent.core.pointers import format_pointer, parse_pointer
from govagent.domain.errors import InvalidSpecError, PointerError, UnsupportedSpecError

_MAPPING_KEY_LINE = re.compile(r"^\s*(- )?[^#\s][^#]*:\s*(#.*)?$")


@dataclass(frozen=True)
class SpecStyle:
    """Formatting detected at load time and reused when dumping."""

    format: Literal["yaml", "json"]
    mapping_indent: int = 2
    sequence_offset: int = 0  # columns between a parent key and the `-` of its items
    explicit_start: bool = False  # document starts with `---`
    json_indent: int | str | None = 2  # None: single-line JSON; str: tab indentation
    json_compact: bool = False  # single-line JSON without spaces after `,` and `:`
    trailing_newline: bool = True


@dataclass
class SpecDocument:
    root: dict[str, Any]  # a ruamel CommentedMap (round-trip node)
    style: SpecStyle

    def copy(self) -> "SpecDocument":
        return SpecDocument(copy.deepcopy(self.root), self.style)


def load_spec(text: str, *, validate_openapi: bool = True) -> SpecDocument:
    """Parse a single-file OpenAPI 3.0/3.1 spec. Raises InvalidSpecError / UnsupportedSpecError."""
    style = _detect_style(text)
    try:
        if style.format == "json":
            root = json.loads(text, object_pairs_hook=CommentedMap)
        else:
            root = _yaml(style).load(text)  # pyright: ignore[reportUnknownMemberType]
    except (YAMLError, json.JSONDecodeError) as exc:
        raise InvalidSpecError(f"cannot parse spec: {exc}") from exc
    if not isinstance(root, CommentedMap):
        raise InvalidSpecError("spec root must be a mapping")

    version = str(root.get("openapi", ""))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    if not re.match(r"^3\.[01]\.\d+$", version):
        raise UnsupportedSpecError(f"only OpenAPI 3.0.x and 3.1.x are supported, got {version!r}")

    external = sorted(set(_external_refs(root, [])))
    if external:
        raise UnsupportedSpecError(
            "multi-file specs are not supported (external $ref found at "
            + ", ".join(external)
            + ")"
        )

    if validate_openapi:
        validate_spec(root)
    return SpecDocument(root, style)


def dump_spec(spec: SpecDocument) -> str:
    style = spec.style
    if style.format == "json":
        separators = (
            (",", ":") if style.json_compact else (", " if style.json_indent is None else ",", ": ")
        )
        text = json.dumps(
            to_plain(spec.root), indent=style.json_indent, separators=separators, ensure_ascii=False
        )
        return text + "\n" if style.trailing_newline else text
    buffer = io.StringIO()
    _yaml(style).dump(spec.root, buffer)  # pyright: ignore[reportUnknownMemberType]
    text = buffer.getvalue()
    return text if style.trailing_newline else text.rstrip("\n")


def validate_spec(root: Any) -> None:
    """Structural OpenAPI validation (openapi-spec-validator). Raises InvalidSpecError."""
    try:
        validate(to_plain(root))
    except OpenAPIValidationError as exc:
        raise InvalidSpecError(
            f"invalid OpenAPI document at {exc.json_path}: {exc.message}"
        ) from exc
    except ValidatorDetectError as exc:
        raise InvalidSpecError(f"invalid OpenAPI document: {exc}") from exc


def resolve(root: Any, pointer: str) -> Any:
    """Return the node at `pointer`. Raises PointerError if a segment does not exist."""
    node = root
    for depth, segment in enumerate(parse_pointer(pointer)):
        if isinstance(node, dict) and segment in node:
            node = cast(dict[str, Any], node)[segment]
        elif isinstance(node, list) and _is_index(segment, len(cast(list[Any], node))):
            node = cast(list[Any], node)[int(segment)]
        else:
            missing = format_pointer(parse_pointer(pointer)[: depth + 1])
            raise PointerError(f"{missing} does not exist")
    return node


def to_plain(node: Any) -> Any:
    """Convert ruamel nodes to plain dict/list/scalars (for validators and JSON output)."""
    if isinstance(node, dict):
        return {str(k): to_plain(v) for k, v in cast(dict[Any, Any], node).items()}
    if isinstance(node, list):
        return [to_plain(v) for v in cast(list[Any], node)]
    if isinstance(node, bool):  # before int: ruamel's ScalarBoolean subclasses int
        return bool(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    if isinstance(node, str):
        return str(node)
    return node


def _is_index(segment: str, length: int) -> bool:
    return re.fullmatch(r"0|[1-9][0-9]*", segment) is not None and int(segment) < length


def _external_refs(node: Any, path: list[str | int]) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in cast(dict[str, Any], node).items():
            if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                found.append(format_pointer([*path, key]))
            else:
                found.extend(_external_refs(value, [*path, key]))
    elif isinstance(node, list):
        for index, value in enumerate(cast(list[Any], node)):
            found.extend(_external_refs(value, [*path, index]))
    return found


def _yaml(style: SpecStyle) -> YAML:
    yaml = YAML()  # round-trip
    yaml.preserve_quotes = True
    yaml.width = 4096  # never re-wrap long lines
    yaml.brace_single_entry_mapping_in_flow_sequence = True  # keep `[{name: pets}]` braced
    yaml.indent(
        mapping=style.mapping_indent,
        sequence=style.sequence_offset + 2,
        offset=style.sequence_offset,
    )
    yaml.explicit_start = style.explicit_start
    return yaml


def _detect_style(text: str) -> SpecStyle:
    trailing_newline = text.endswith("\n")
    if text.lstrip().startswith("{"):
        lines = text.strip().splitlines()
        json_indent: int | str | None = None
        if len(lines) > 1:
            second = lines[1]
            whitespace = second[: len(second) - len(second.lstrip())]
            json_indent = whitespace if "\t" in whitespace else len(whitespace)
        return SpecStyle(
            "json",
            json_indent=json_indent,
            json_compact=json_indent is None and re.search(r'":\s', text) is None,
            trailing_newline=trailing_newline,
        )

    mapping_indent: int | None = None
    sequence_offset: int | None = None
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    for parent, child in itertools.pairwise(lines):
        if not _MAPPING_KEY_LINE.match(parent) or parent.lstrip().startswith("- "):
            continue  # only `key:` lines opening a block
        parent_col = len(parent) - len(parent.lstrip())
        child_col = len(child) - len(child.lstrip())
        if child.lstrip().startswith("- "):
            if sequence_offset is None:
                sequence_offset = child_col - parent_col
        elif mapping_indent is None and child_col > parent_col:
            mapping_indent = child_col - parent_col
        if mapping_indent is not None and sequence_offset is not None:
            break
    return SpecStyle(
        "yaml",
        mapping_indent=mapping_indent or 2,
        sequence_offset=sequence_offset if sequence_offset is not None else 0,
        explicit_start=text.startswith("---"),
        trailing_newline=trailing_newline,
    )
