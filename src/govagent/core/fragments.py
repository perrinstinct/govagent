"""Render the part of the spec an LLM needs to see, bounded in depth and size."""

import io
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

from govagent.core.spec_io import resolve, to_plain

_MAP = "__govagent_collapsed_map__"
_SEQ = "__govagent_collapsed_seq__"


def render_fragment(
    root: dict[str, Any], pointer: str, max_depth: int = 4, max_chars: int = 8000
) -> str:
    """YAML of the subtree at `pointer`; nodes deeper than `max_depth` become `{…}` / `[…]`.

    For `/paths` only the path keys are rendered. If the text exceeds `max_chars`, the depth is
    reduced; as a last resort the text is cut and marked as truncated.
    """
    node = to_plain(resolve(root, pointer))
    depth = 1 if pointer == "/paths" else max_depth
    text = _render(_collapse(node, depth))
    while len(text) > max_chars and depth > 1:
        depth -= 1
        text = _render(_collapse(node, depth))
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n# … truncated\n"
    return text


def _collapse(node: Any, depth: int) -> Any:
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        if depth == 0 and mapping:
            return _MAP
        return {key: _collapse(value, depth - 1) for key, value in mapping.items()}
    if isinstance(node, list):
        items = cast(list[Any], node)
        if depth == 0 and items:
            return _SEQ
        return [_collapse(value, depth - 1) for value in items]
    if isinstance(node, str) and "\n" in node:
        return LiteralScalarString(node)  # readable `|` block instead of an escaped string
    return node


def _render(node: Any) -> str:
    yaml = YAML()
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    buffer = io.StringIO()
    yaml.dump(node, buffer)  # pyright: ignore[reportUnknownMemberType]
    return buffer.getvalue().replace(_MAP, "{…}").replace(_SEQ, "[…]")
