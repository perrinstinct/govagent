import json
from pathlib import Path

import pytest

from govagent.core.spec_io import dump_spec, load_spec, resolve
from govagent.domain.errors import InvalidSpecError, PointerError, UnsupportedSpecError

SPECS = Path(__file__).resolve().parents[1] / "fixtures" / "specs"
COMMENTED = (SPECS / "commented.yaml").read_text()

MINIMAL = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}}


def test_unmodified_commented_yaml_round_trips_byte_identical() -> None:
    assert dump_spec(load_spec(COMMENTED)) == COMMENTED


@pytest.mark.parametrize(
    "text",
    [
        # 4-space mappings, unindented sequences, explicit document start
        "---\nopenapi: 3.1.0\ninfo:\n    title: t\n    version: '1'\ntags:\n- name: a\n"
        "- name: b\n  description: second\npaths: {}\n",
        # 4-space mappings, items written `-   key:` (content aligned on the mapping indent)
        "openapi: 3.1.0\ninfo:\n    title: t\n    version: '1'\nservers:\n-   url: https://a\n"
        "    description: x\npaths: {}\n",
        # no trailing newline
        "openapi: 3.1.0\ninfo:\n  title: t\n  version: '1'\npaths: {}",
    ],
)
def test_yaml_style_variants_round_trip(text: str) -> None:
    assert dump_spec(load_spec(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(MINIMAL, indent=2) + "\n",
        json.dumps(MINIMAL, indent=4),
        json.dumps(MINIMAL, indent="\t") + "\n",
        json.dumps(MINIMAL, separators=(",", ":")),
        json.dumps(MINIMAL) + "\n",
        json.dumps({**MINIMAL, "info": {"title": "café ☕", "version": "1"}}, ensure_ascii=False),
    ],
)
def test_json_round_trips_with_detected_style(text: str) -> None:
    spec = load_spec(text)

    assert spec.style.format == "json"
    assert dump_spec(spec) == text


def test_copy_is_independent() -> None:
    spec = load_spec(COMMENTED)
    clone = spec.copy()
    clone.root["info"]["title"] = "Changed"

    assert spec.root["info"]["title"] == "Pet Store"


def test_resolve_follows_pointer() -> None:
    root = load_spec(COMMENTED).root

    assert resolve(root, "/paths/~1pets~1{petId}/get/operationId") == "getPet"
    assert resolve(root, "/servers/1/url") == "https://staging.example.com/v1"
    assert resolve(root, "") is root


@pytest.mark.parametrize("pointer", ["/paths/~1nope", "/servers/2", "/servers/01", "/info/title/x"])
def test_resolve_rejects_missing_targets(pointer: str) -> None:
    with pytest.raises(PointerError):
        resolve(load_spec(COMMENTED).root, pointer)


def test_external_ref_is_rejected() -> None:
    text = COMMENTED.replace('"#/components/schemas/Pet"', '"./pet.yaml"', 1)

    with pytest.raises(UnsupportedSpecError, match="external"):
        load_spec(text)


@pytest.mark.parametrize(
    "text",
    [
        "swagger: '2.0'\ninfo: {title: t, version: '1'}\npaths: {}\n",
        "openapi: 3.2.0\ninfo: {title: t, version: '1'}\npaths: {}\n",
    ],
)
def test_unsupported_versions_are_rejected(text: str) -> None:
    with pytest.raises(UnsupportedSpecError, match=r"3\.0\.x and 3\.1\.x"):
        load_spec(text)


@pytest.mark.parametrize(
    "text",
    [
        "openapi: 3.0.3\ninfo: [unclosed\n",
        "- just\n- a list\n",
        '{"openapi": "3.1.0",',
        "openapi: 3.0.3\ninfo: {title: t}\npaths: {}\n",  # missing info.version
    ],
)
def test_invalid_documents_are_rejected(text: str) -> None:
    with pytest.raises(InvalidSpecError):
        load_spec(text)


def test_dangling_internal_ref_is_an_invalid_spec() -> None:
    text = COMMENTED.replace('"#/components/schemas/Pet"', '"#/components/schemas/Animal"', 1)

    with pytest.raises(InvalidSpecError, match="unresolvable"):
        load_spec(text)


def test_validation_can_be_skipped() -> None:
    spec = load_spec("openapi: 3.0.3\ninfo: {title: t}\npaths: {}\n", validate_openapi=False)

    assert spec.root["info"]["title"] == "t"
