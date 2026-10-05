import pytest

from govagent.domain.errors import PatchError, ScopeError
from govagent.interfaces.skeleton import PatchOp, _check_scope, _from_pointer, _to_pointer


def test_pointer_escapes_slash_and_tilde() -> None:
    assert _to_pointer(["paths", "/pets/{id}", "get"]) == "/paths/~1pets~1{id}/get"
    assert _to_pointer(["x", "a~b", 0]) == "/x/a~0b/0"


def test_pointer_round_trip() -> None:
    segments = ["paths", "/a~/b", "post"]

    assert _from_pointer(_to_pointer(segments)) == segments


def test_from_pointer_rejects_relative_pointer() -> None:
    with pytest.raises(PatchError):
        _from_pointer("paths/x")


def test_scope_allows_scope_itself_and_descendants() -> None:
    _check_scope(
        PatchOp(op="add", path="/paths/~1pets/post/summary", value="x"), "/paths/~1pets/post"
    )
    _check_scope(PatchOp(op="replace", path="/paths/~1pets/post", value={}), "/paths/~1pets/post")


@pytest.mark.parametrize(
    "path",
    ["/paths/~1pets/get/summary", "/paths/~1pets/postfix", "/info/title"],
)
def test_scope_rejects_outside_paths(path: str) -> None:
    with pytest.raises(ScopeError):
        _check_scope(PatchOp(op="add", path=path, value="x"), "/paths/~1pets/post")
