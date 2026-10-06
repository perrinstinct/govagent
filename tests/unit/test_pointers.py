import pytest

from govagent.core.pointers import format_pointer, is_within, normalize_scope, parse_pointer
from govagent.domain.errors import PointerError


@pytest.mark.parametrize(
    ("segments", "pointer"),
    [
        ([], ""),
        (["paths", "/pets/{id}", "get"], "/paths/~1pets~1{id}/get"),
        (["x", "a~b", "c~/d"], "/x/a~0b/c~0~1d"),
        (["servers", 0, "url"], "/servers/0/url"),
        ([""], "/"),
    ],
)
def test_format_and_parse_are_inverse(segments: list[str | int], pointer: str) -> None:
    assert format_pointer(segments) == pointer
    assert parse_pointer(pointer) == [str(s) for s in segments]


def test_tilde_one_is_not_double_unescaped() -> None:
    # "~01" is an escaped "~" followed by "1", not "/".
    assert parse_pointer("/a~01") == ["a~1"]


def test_relative_pointer_is_rejected() -> None:
    with pytest.raises(PointerError):
        parse_pointer("paths/x")


@pytest.mark.parametrize(
    ("pointer", "scope", "expected"),
    [
        ("/paths/~1pets/get", "/paths/~1pets/get", True),
        ("/paths/~1pets/get/summary", "/paths/~1pets/get", True),
        ("/paths/~1petsx/get", "/paths/~1pets", False),
        ("/paths", "/paths/~1pets", False),
        ("/anything", "", True),
    ],
)
def test_is_within_is_segment_aware(pointer: str, scope: str, expected: bool) -> None:
    assert is_within(pointer, scope) is expected


def test_slash_scope_means_root() -> None:
    assert normalize_scope("/") == ""
    assert normalize_scope("/info") == "/info"
