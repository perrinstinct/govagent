from pathlib import Path

from govagent.core.fragments import render_fragment
from govagent.core.spec_io import load_spec

ROOT = load_spec(
    (Path(__file__).resolve().parents[1] / "fixtures" / "specs" / "commented.yaml").read_text()
).root


def test_renders_subtree_as_yaml() -> None:
    text = render_fragment(ROOT, "/paths/~1pets~1{petId}/get")

    assert text.startswith("tags:\n  - pets\nsummary: Get a pet\n")
    assert "operationId: getPet" in text
    assert "  '404':\n    $ref: '#/components/responses/Problem'\n" in text


def test_multiline_strings_render_as_literal_blocks() -> None:
    text = render_fragment(ROOT, "/info")

    assert "description: |\n  Manage the pets of the store.\n\n  Multi-paragraph" in text


def test_collapses_nodes_deeper_than_max_depth() -> None:
    text = render_fragment(ROOT, "/paths/~1pets/get", max_depth=2)

    assert "responses:\n  '200': {…}\n  '400': {…}\n" in text
    assert "parameters:\n  - {…}\n" in text


def test_paths_scope_renders_keys_only() -> None:
    assert render_fragment(ROOT, "/paths") == "/pets: {…}\n/pets/{petId}: {…}\n"


def test_empty_containers_are_not_collapsed() -> None:
    text = render_fragment(ROOT, "/security/0", max_depth=1)

    assert text == "apiKey: []\n"


def test_reduces_depth_then_truncates_to_fit_max_chars() -> None:
    shallow = render_fragment(ROOT, "", max_depth=4, max_chars=400)
    tiny = render_fragment(ROOT, "", max_depth=4, max_chars=60)

    assert len(shallow) <= 400
    assert "{…}" in shallow
    assert tiny.endswith("# … truncated\n")
    assert len(tiny) <= 60 + len("# … truncated\n")
