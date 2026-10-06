"""Patch ops on commented YAML: assert the exact textual diff, not just the data."""

import difflib
from pathlib import Path
from typing import Any

import pytest

from govagent.core.patching import apply_patch
from govagent.core.spec_io import SpecDocument, dump_spec, load_spec
from govagent.domain.errors import PatchError
from govagent.domain.models import PatchOp

COMMENTED = (
    Path(__file__).resolve().parents[1] / "fixtures" / "specs" / "commented.yaml"
).read_text()
LIST_PETS = "/paths/~1pets/get"


def patched_text(*ops: PatchOp) -> str:
    spec = load_spec(COMMENTED)
    return dump_spec(SpecDocument(apply_patch(spec.root, list(ops)), spec.style))


def diff(*ops: PatchOp) -> list[str]:
    lines = difflib.unified_diff(
        COMMENTED.splitlines(), patched_text(*ops).splitlines(), lineterm="", n=0
    )
    return [line for line in lines if line[:1] in "+-" and line[:3] not in ("+++", "---")]


def op(kind: str, path: str, value: Any = ..., from_: str | None = None) -> PatchOp:
    """Build an op the way model output arrives: through validation, `from` alias included."""
    data: dict[str, Any] = {"op": kind, "path": path}
    if value is not ...:
        data["value"] = value
    if from_ is not None:
        data["from"] = from_
    return PatchOp.model_validate(data)


# --- add ----------------------------------------------------------------------------------------


def test_add_new_key_at_conventional_position_in_operation() -> None:
    text = COMMENTED.replace("      summary: List pets\n", "", 1)
    spec = load_spec(text)
    root = apply_patch(spec.root, [op("add", f"{LIST_PETS}/summary", "List pets")])

    assert dump_spec(SpecDocument(root, spec.style)) == COMMENTED


def test_add_unknown_key_is_appended() -> None:
    assert diff(op("add", "/info/x-team", "pets")) == ["+  x-team: pets"]


def test_add_on_existing_key_replaces_value_and_keeps_comment() -> None:
    assert diff(op("add", "/info/contact/email", "pets@example.com")) == [
        "-    email: api-team@example.com # shared team mailbox",
        "+    email: pets@example.com # shared team mailbox",
    ]


def test_add_nested_mapping_uses_document_indentation() -> None:
    assert diff(op("add", "/components/schemas/Pet/properties/tag", {"type": "string"})) == [
        "+        tag:",
        "+          type: string",
    ]


def test_add_inserts_into_array_at_index() -> None:
    lines = diff(op("add", "/components/schemas/Pet/required/1", "age"))

    assert lines == ["-      required: [id, name]", "+      required: [id, age, name]"]


def test_add_appends_with_dash() -> None:
    assert diff(op("add", "/components/schemas/Pet/required/-", "age")) == [
        "-      required: [id, name]",
        "+      required: [id, name, age]",
    ]


@pytest.mark.parametrize(
    "path",
    ["/components/schemas/Nope/type", "/components/schemas/Pet/required/5", "/servers/01/x"],
)
def test_add_fails_on_missing_parent_or_bad_index(path: str) -> None:
    with pytest.raises(PatchError):
        patched_text(op("add", path, "x"))


# --- remove -------------------------------------------------------------------------------------


def test_remove_mapping_key() -> None:
    assert diff(op("remove", "/info/x-audience")) == ["-  x-audience: 'external'"]


def test_remove_array_item() -> None:
    assert diff(op("remove", "/components/schemas/Pet/required/0")) == [
        "-      required: [id, name]",
        "+      required: [name]",
    ]


def test_remove_missing_target_fails() -> None:
    with pytest.raises(PatchError, match="does not exist"):
        patched_text(op("remove", "/info/x-nope"))


# --- replace ------------------------------------------------------------------------------------


def test_replace_quoted_scalar_keeps_its_quote_style() -> None:
    assert diff(op("replace", "/info/x-audience", "partners")) == [
        "-  x-audience: 'external'",
        "+  x-audience: 'partners'",
    ]


def test_replace_keeps_aligned_comment_gap() -> None:
    text = COMMENTED.replace("name: X-API-Key", "name: X-API-Key   # header name")
    spec = load_spec(text)
    root = apply_patch(spec.root, [op("replace", "/components/securitySchemes/apiKey/name", "Key")])

    assert "      name: Key   # header name\n" in dump_spec(SpecDocument(root, spec.style))


def test_replace_scalar_keeps_comment() -> None:
    assert diff(op("replace", "/servers/0/url", "https://api.example.org/v1")) == [
        "-  - url: https://api.example.com/v1 # production",
        "+  - url: https://api.example.org/v1 # production",
    ]


def test_replace_array_item() -> None:
    assert diff(op("replace", "/components/schemas/Pet/required/1", "label")) == [
        "-      required: [id, name]",
        "+      required: [id, label]",
    ]


def test_replace_missing_target_fails() -> None:
    with pytest.raises(PatchError):
        patched_text(op("replace", f"{LIST_PETS}/deprecated", True))


# --- move ---------------------------------------------------------------------------------------


PET_PROPS = "/components/schemas/Pet/properties"


def test_move_within_mapping_renames_in_place_with_comment() -> None:
    rename = op("move", f"{PET_PROPS}/displayName", from_=f"{PET_PROPS}/name")

    assert diff(rename) == ["-        name:", "+        displayName:"]
    assert "type: string # display name, not unique" in patched_text(rename)


def test_move_path_key_keeps_its_position() -> None:
    lines = diff(op("move", "/paths/~1animals", from_="/paths/~1pets"))

    assert lines == ["-  /pets:", "+  /animals:"]


def test_move_across_parents() -> None:
    text = patched_text(
        op("move", "/components/schemas/Problem/properties/age", from_=f"{PET_PROPS}/age")
    )
    root = load_spec(text).root

    assert "age" not in root["components"]["schemas"]["Pet"]["properties"]
    assert root["components"]["schemas"]["Problem"]["properties"]["age"]["type"] == "integer"


def test_move_into_own_descendant_fails() -> None:
    with pytest.raises(PatchError, match="descendants"):
        patched_text(op("move", "/info/contact/inner", from_="/info"))


def test_move_from_missing_source_fails() -> None:
    with pytest.raises(PatchError):
        patched_text(op("move", "/info/x-b", from_="/info/x-nope"))


def test_move_onto_itself_is_a_no_op() -> None:
    assert diff(op("move", "/info/title", from_="/info/title")) == []


# --- invariants ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["add", "replace", "remove"])
def test_root_operations_are_refused(kind: str) -> None:
    with pytest.raises(PatchError, match="root"):
        patched_text(op(kind, "", {}))


@pytest.mark.parametrize("kind", ["add", "replace"])
def test_value_is_required_for_add_and_replace(kind: str) -> None:
    with pytest.raises(PatchError, match="requires a value"):
        patched_text(op(kind, "/info/title"))


def test_move_requires_from() -> None:
    with pytest.raises(PatchError, match="from"):
        patched_text(op("move", "/info/x-b"))


def test_patch_is_atomic_and_leaves_input_untouched() -> None:
    spec = load_spec(COMMENTED)
    ops = [op("replace", "/info/title", "Changed"), op("remove", "/info/x-nope")]

    with pytest.raises(PatchError, match="op #1"):
        apply_patch(spec.root, ops)
    assert dump_spec(spec) == COMMENTED
