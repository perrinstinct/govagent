from typing import Any

from govagent.core.integrity import dangling_required


def schema(required: list[str], properties: dict[str, Any] | None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "object", "required": required}
    if properties is not None:
        node["properties"] = properties
    return node


def test_finds_required_names_missing_from_properties() -> None:
    root = {"components": {"schemas": {"Pet": schema(["petName", "id"], {"id": {}})}}}

    assert dangling_required(root) == {("/components/schemas/Pet", "petName")}


def test_nested_and_inline_schemas_are_checked() -> None:
    owner = schema(["first_name"], {"firstName": {}})
    root = {"paths": {"/p": {"post": {"x": [schema(["a"], {"a": {}, "owner": owner})]}}}}

    assert dangling_required(root) == {("/paths/~1p/post/x/0/properties/owner", "first_name")}


def test_schema_without_properties_is_not_checked() -> None:
    root = {"allOf": [{"$ref": "#/components/schemas/Base"}, schema(["name"], None)]}

    assert dangling_required(root) == set()


def test_consistent_schema_is_clean() -> None:
    assert dangling_required(schema(["a", "b"], {"a": {}, "b": {}})) == set()
