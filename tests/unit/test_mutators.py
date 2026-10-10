"""Mutators on a real seed: the injected change is small, valid, deterministic (no Spectral)."""

import difflib
import random
from pathlib import Path
from typing import Any

import pytest

from evals.mutators import MUTATORS, Mutation
from govagent.core.patching import apply_patch
from govagent.core.spec_io import SpecDocument, dump_spec, load_spec, resolve

SEEDS = Path(__file__).resolve().parents[2] / "evals" / "seeds"
PETSTORE = (SEEDS / "petstore.yaml").read_text()
MINIMAL = "openapi: 3.0.3\ninfo:\n  title: t\n  version: '1'\npaths: {}\n"


def mutate(text: str, rule_id: str, seed: str = "s") -> tuple[Mutation, str, Any]:
    spec = load_spec(text)
    mutation = MUTATORS[rule_id](spec.root, random.Random(seed))
    assert mutation is not None
    root = apply_patch(spec.root, mutation.ops)
    return mutation, dump_spec(SpecDocument(root, spec.style)), root


def changed_lines(before: str, after: str) -> list[str]:
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")]


@pytest.mark.parametrize("rule_id", list(MUTATORS))
def test_mutation_is_valid_small_and_keeps_comments(rule_id: str) -> None:
    mutation, text, _ = mutate(PETSTORE, rule_id)

    assert mutation.rule_id == rule_id
    load_spec(text)  # still a valid OpenAPI document
    assert "# team mailbox" in text
    assert 1 <= len(changed_lines(PETSTORE, text)) <= 8


@pytest.mark.parametrize("rule_id", list(MUTATORS))
def test_same_rng_seed_gives_the_same_mutation(rule_id: str) -> None:
    first, _, _ = mutate(PETSTORE, rule_id, "seed-1")
    second, _, _ = mutate(PETSTORE, rule_id, "seed-1")

    assert first == second


@pytest.mark.parametrize("rule_id", list(MUTATORS))
def test_no_eligible_location_returns_none(rule_id: str) -> None:
    assert MUTATORS[rule_id](load_spec(MINIMAL).root, random.Random(0)) is None


def test_path_kebab_camel_cases_a_hyphenated_segment_and_keeps_position() -> None:
    spec = load_spec(PETSTORE)
    seen: set[str] = set()
    for seed in range(20):
        mutation = MUTATORS["gov-path-kebab"](spec.root, random.Random(seed))
        assert mutation is not None
        seen.add(mutation.pointer)
    assert seen == {
        "/paths/~1Pets",
        "/paths/~1Pets~1{petId}",
        "/paths/~1petOwners~1{ownerId}~1pets",
    }


def test_property_rename_also_updates_required() -> None:
    for seed in range(30):
        mutation, _, root = mutate(PETSTORE, "gov-property-camel", str(seed))
        if mutation.pointer == "/components/schemas/Pet/properties/display_name":
            assert root["components"]["schemas"]["Pet"]["required"] == ["petId", "display_name"]
            return
    pytest.fail("displayName was never picked")


def test_problem_json_targets_shared_responses_where_they_are_defined() -> None:
    pointers = {mutate(PETSTORE, "gov-problem-json", str(seed))[0].pointer for seed in range(20)}

    assert pointers == {
        "/components/responses/BadRequest/content",
        "/components/responses/NotFound/content",
    }


def test_error_response_removes_all_4xx_and_keeps_a_response() -> None:
    mutation, _, root = mutate(PETSTORE, "gov-error-response")
    responses = resolve(root, mutation.pointer)

    assert responses
    assert not any(str(code).startswith("4") for code in responses)
