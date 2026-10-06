from govagent.core.scope_guard import scope_violations
from govagent.domain.models import PatchOp

SCOPE = "/paths/~1pets/post"


def test_ops_inside_scope_are_allowed() -> None:
    ops = [
        PatchOp(op="add", path=f"{SCOPE}/summary", value="Create a pet"),
        PatchOp(op="replace", path=SCOPE, value={}),
    ]

    assert scope_violations(ops, SCOPE) == []


def test_extra_write_scopes_are_allowed() -> None:
    ops = [PatchOp(op="add", path="/components/responses/Problem", value={})]

    assert scope_violations(ops, SCOPE, ["/components/responses"]) == []


def test_sibling_with_common_prefix_is_rejected() -> None:
    ops = [PatchOp(op="add", path="/paths/~1pets/postx", value={})]

    assert len(scope_violations(ops, SCOPE)) == 1


def test_move_checks_both_from_and_path() -> None:
    inside_to_outside = PatchOp.model_validate(
        {"op": "move", "path": "/info/x", "from": f"{SCOPE}/summary"}
    )
    outside_to_inside = PatchOp.model_validate(
        {"op": "move", "path": f"{SCOPE}/x", "from": "/info/title"}
    )

    problems = scope_violations([inside_to_outside, outside_to_inside], SCOPE)

    assert len(problems) == 2
    assert "op #0: move path /info/x" in problems[0]
    assert "op #1: move from /info/title" in problems[1]


def test_root_scope_allows_everything_but_the_root_itself() -> None:
    assert scope_violations([PatchOp(op="add", path="/security", value=[])], "/") == []
    assert scope_violations([PatchOp(op="replace", path="", value={})], "/") != []


def test_invalid_pointer_is_reported_not_raised() -> None:
    problems = scope_violations([PatchOp(op="add", path="info/x", value=1)], "/info")

    assert len(problems) == 1
    assert "invalid path" in problems[0]
