"""Pointer hints on the two escaping mistakes Haiku 4.5 made in the full eval run."""

from typing import Any

from govagent.core.pointer_hints import correct_pointer, pointer_hints
from govagent.core.spec_io import load_spec
from govagent.domain.models import PatchOp

SPEC = """\
openapi: 3.0.3
info:
  title: t
  version: '1'
paths:
  /tickets/{ticketId}:
    delete:
      responses:
        '502':
          description: x
          content:
            application/json: {}
  /devices/{deviceId}/telemetry-readings/:
    get:
      responses:
        '200':
          description: ok
"""
ROOT: Any = load_spec(SPEC, validate_openapi=False).root
DELETE = "/paths/~1tickets~1{ticketId}/delete"
READINGS = "/paths/~1devices~1{deviceId}~1telemetry-readings~1"


def op(**fields: Any) -> PatchOp:
    return PatchOp.model_validate(fields)


def test_http_method_glued_into_the_path_key_is_corrected() -> None:
    glued = "/paths/~1tickets~1{ticketId}~1delete/responses/502/content"

    assert correct_pointer(ROOT, glued) == f"{DELETE}/responses/502/content"


def test_correction_keeps_the_part_that_does_not_exist_yet() -> None:
    new_key = "/paths/~1tickets~1{ticketId}~1delete/responses/502/content/application~1problem+json"

    assert correct_pointer(ROOT, new_key) == (
        f"{DELETE}/responses/502/content/application~1problem+json"
    )


def test_valid_pointer_needs_no_correction() -> None:
    assert correct_pointer(ROOT, f"{DELETE}/responses") is None


def test_hint_for_a_glued_method_suggests_the_escaped_pointer() -> None:
    glued = "/paths/~1tickets~1{ticketId}~1delete/responses/502/content"

    [hint] = pointer_hints(ROOT, [op(op="replace", path=glued, value={})])

    assert f"did you mean {DELETE}/responses/502/content ?" in hint


def test_hint_for_a_stray_slash_after_a_trailing_slash_key() -> None:
    stray = f"{READINGS}/"

    [hint] = pointer_hints(ROOT, [op(op="move", **{"from": stray}, path=READINGS[:-2])])

    assert hint.startswith(f"move from {stray}:")
    assert f"{READINGS} exists, but '/' below it does not" in hint


def test_valid_ops_and_new_keys_get_no_hint() -> None:
    ops = [
        op(op="add", path=f"{DELETE}/summary", value="Cancel a ticket"),
        op(op="replace", path=f"{DELETE}/responses/502/description", value="y"),
    ]

    assert pointer_hints(ROOT, ops) == []
