"""Consistency checks a fix must not break, beyond what the linter and validator see.

openapi-spec-validator accepts a `required` entry naming a property that does not exist, and
no governance rule flags it, so a rename that forgets to update `required` would pass
verification. Only dangling entries *created* by a fix count: pre-existing ones are not the
fix's fault.
"""

from typing import Any, cast

from govagent.core.pointers import format_pointer


def dangling_required(root: Any) -> set[tuple[str, str]]:
    """`(schema pointer, name)` for each `required` name missing from the schema's properties.

    Only schemas that declare `properties` are checked: a schema may legitimately list
    `required` names defined elsewhere (e.g. in an `allOf` sibling).
    """
    found: set[tuple[str, str]] = set()
    _walk(root, [], found)
    return found


def _walk(node: Any, path: list[str | int], found: set[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        required, properties = mapping.get("required"), mapping.get("properties")
        if isinstance(required, list) and isinstance(properties, dict):
            names = cast(dict[str, Any], properties)
            for name in cast(list[Any], required):
                if isinstance(name, str) and name not in names:
                    found.add((format_pointer(path), name))
        for key, value in mapping.items():
            _walk(value, [*path, key], found)
    elif isinstance(node, list):
        for index, value in enumerate(cast(list[Any], node)):
            _walk(value, [*path, index], found)
