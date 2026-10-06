"""Prompts for the fix model (docs/SPEC.md §6)."""

import json
from collections.abc import Sequence

from govagent.domain.models import PatchOp, RuleMeta, ViolationGroup

SYSTEM_PROMPT = """\
You fix API governance violations in an OpenAPI document. You never rewrite the document: you
return JSON Patch operations (RFC 6902) that a program applies and then verifies by re-linting.

Rules:
- Make the minimal change that resolves the listed violations, and nothing else.
- Use only the operations add, remove, replace and move. No copy, no test.
- JSON pointers are absolute, from the document root (RFC 6901). Escape "~" as "~0" and "/" as
  "~1" inside a key: the path key /pets/{id} is written /paths/~1pets~1{id}.
- Write only under the allowed scopes listed in the request. Any other operation is rejected.
- Preserve the API's semantics. Do not invent business facts (emails, owners, auth schemes).
- When renaming a key, use `move`, and update every reference inside the allowed scopes
  (`required` lists, examples, `$ref`s).
- If the fix requires business knowledge that cannot be inferred from the document, return
  needs_human_input=true with no operations.
"""


def render_user_prompt(
    group: ViolationGroup,
    meta: RuleMeta,
    allowed_scopes: Sequence[str],
    fragment: str,
    context: Sequence[tuple[str, str]],
    feedback: str | None,
) -> str:
    lines = [
        f"Rule: {group.rule_id}",
        f"Guidance: {meta.guidance}",
        "Violations:",
        *(f"- {v.pointer or '/'}: {v.message}" for v in group.violations),
        "Allowed scopes (write only under these pointers):",
        *(f"- {scope or '/'}" for scope in allowed_scopes),
        "",
        f"Document fragment at {group.scope_pointer or '/'} (YAML, deeper nodes shown as {{…}}):",
        fragment.rstrip("\n"),
    ]
    for pointer, text in context:
        lines += ["", f"Existing content at {pointer} (for reference):", text.rstrip("\n")]
    if feedback:
        lines += ["", "Your previous attempt was rejected:", feedback]
    return "\n".join(lines) + "\n"


def render_ops(ops: Sequence[PatchOp]) -> str:
    return json.dumps([op.model_dump(by_alias=True, exclude_none=True) for op in ops])
