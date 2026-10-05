"""M1 walking skeleton: lint -> one LLM call -> patch -> re-lint, for a single rule.

Deliberately quick and dirty, reachable only through `govagent fix --skeleton`. Every piece
here is replaced by the real implementation in M2 (core/, adapters/spectral.py) and M3
(agent/, adapters/bedrock.py); delete this module then.
"""

import io
import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from govagent.config import Settings
from govagent.domain.errors import ConfigError, LinterError, LLMError, PatchError, ScopeError

RULE_ID = "gov-operation-summary"

SYSTEM_PROMPT = """\
You fix API governance violations in an OpenAPI document by returning JSON Patch operations.
Rules:
- Make the minimal change that resolves the listed violations.
- Use only the operations add, remove, replace and move.
- JSON pointers are absolute, from the document root (RFC 6901: "/" in a key is escaped "~1").
- Never write outside the allowed scope pointer.
- Preserve the API's semantics.
- If the fix requires business knowledge you cannot infer from the document, return
  needs_human_input=true and no operations.
"""


class PatchOp(BaseModel):
    """One JSON Patch operation (RFC 6902 subset)."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["add", "remove", "replace", "move"]
    path: str = Field(description="Absolute JSON pointer of the target location.")
    value: Any | None = Field(default=None, description="Value for add/replace.")
    from_: str | None = Field(default=None, alias="from", description="Source pointer for move.")


class LLMFixOutput(BaseModel):
    """Structured output requested from the model."""

    ops: list[PatchOp] = Field(description="JSON Patch operations, in application order.")
    rationale: str = Field(description="1-3 sentences explaining the fix.")
    needs_human_input: bool = Field(
        description="True when the fix requires business knowledge; ops must then be empty."
    )


@dataclass(frozen=True)
class Proposal:
    output: LLMFixOutput
    input_tokens: int
    output_tokens: int


# (system prompt, user prompt) -> proposal. Bedrock in production, scripted in tests.
Proposer = Callable[[str, str], Proposal]


@dataclass(frozen=True)
class SkeletonResult:
    target: str | None  # fingerprint "rule_id:pointer", None if the spec was already clean
    proposal: Proposal | None
    resolved: bool
    introduced: list[str]  # fingerprints of new error/warn violations
    patched_text: str | None
    cost_usd: float | None


# --- YAML -------------------------------------------------------------------------------------


def _yaml() -> YAML:
    yaml = YAML()  # round-trip: keeps comments, key order and quotes
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def _dump(node: Any) -> str:
    buffer = io.StringIO()
    _yaml().dump(node, buffer)  # pyright: ignore[reportUnknownMemberType]
    return buffer.getvalue()


# --- Spectral ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Violation:
    rule_id: str
    pointer: str
    severity: int  # Spectral: 0 error, 1 warn, 2 info, 3 hint
    message: str

    @property
    def fingerprint(self) -> str:
        return f"{self.rule_id}:{self.pointer}"


def _to_pointer(segments: Sequence[str | int]) -> str:
    return "".join("/" + str(s).replace("~", "~0").replace("/", "~1") for s in segments)


def _from_pointer(pointer: str) -> list[str]:
    if pointer and not pointer.startswith("/"):
        raise PatchError(f"invalid JSON pointer: {pointer!r}")
    return [s.replace("~1", "/").replace("~0", "~") for s in pointer.split("/")[1:]]


def _lint(spec_text: str, settings: Settings) -> list[_Violation]:
    with tempfile.TemporaryDirectory() as tmp:
        spec_file = Path(tmp) / "spec.yaml"
        spec_file.write_text(spec_text)
        cmd = [settings.spectral_bin, "lint", "--quiet", "--format", "json"]
        cmd += ["--ruleset", str(settings.ruleset_path), str(spec_file)]
        # Non-zero exit just means violations were found; only unreadable stdout is a crash.
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    try:
        raw = cast(list[dict[str, Any]], json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        raise LinterError(f"spectral failed (exit {result.returncode}): {result.stderr}") from exc
    return [_Violation(r["code"], _to_pointer(r["path"]), r["severity"], r["message"]) for r in raw]


# --- Patching ---------------------------------------------------------------------------------


def _resolve(doc: Any, segments: list[str]) -> Any:
    node: Any = doc
    for segment in segments:
        if isinstance(node, CommentedMap) and segment in node:
            node = cast(Any, node[segment])
        elif isinstance(node, CommentedSeq) and segment.isdigit() and int(segment) < len(node):
            node = cast(Any, node[int(segment)])
        else:
            raise PatchError(f"path not found at segment {segment!r}")
    return node


def _apply(doc: Any, op: PatchOp) -> None:
    if op.op not in ("add", "replace"):
        raise PatchError(f"the skeleton only supports add/replace, got {op.op!r}")
    *parent_path, key = _from_pointer(op.path)
    parent = _resolve(doc, parent_path)
    if isinstance(parent, CommentedMap):
        if op.op == "replace" and key not in parent:
            raise PatchError(f"replace target does not exist: {op.path}")
        parent[key] = op.value
    elif isinstance(parent, CommentedSeq) and op.op == "add" and key == "-":
        cast(list[Any], parent).append(op.value)
    else:
        raise PatchError(f"unsupported target for {op.op}: {op.path}")


def _check_scope(op: PatchOp, scope: str) -> None:
    for pointer in (op.path, op.from_):
        if pointer is not None and pointer != scope and not pointer.startswith(scope + "/"):
            raise ScopeError(f"{op.op} {pointer} is outside the allowed scope {scope}")


# --- Pipeline ---------------------------------------------------------------------------------


def _user_prompt(target: _Violation, scope: str, fragment: str, guidance: str) -> str:
    return (
        f"Rule: {target.rule_id}\n"
        f"Guidance: {guidance}\n"
        f"Violations:\n- {target.pointer}: {target.message}\n"
        f"Allowed scope pointer (write only under it): {scope}\n"
        f"Fragment at {scope} (YAML):\n{fragment}"
    )


def _guidance(settings: Settings) -> str:
    meta = cast(dict[str, Any], YAML(typ="safe").load(settings.rules_meta_path))  # pyright: ignore[reportUnknownMemberType]
    return str(meta["rules"][RULE_ID]["guidance"])


def _cost(proposal: Proposal, settings: Settings) -> float | None:
    if settings.price_input_per_mtok is None or settings.price_output_per_mtok is None:
        return None
    return (
        proposal.input_tokens * settings.price_input_per_mtok
        + proposal.output_tokens * settings.price_output_per_mtok
    ) / 1_000_000


def run(spec_text: str, settings: Settings, propose: Proposer) -> SkeletonResult:
    """Fix the first `gov-operation-summary` violation with one LLM call, then verify."""
    before = _lint(spec_text, settings)
    target = next((v for v in before if v.rule_id == RULE_ID), None)
    if target is None:
        return SkeletonResult(None, None, True, [], None, None)

    doc: Any = _yaml().load(spec_text)  # pyright: ignore[reportUnknownMemberType]
    scope = _to_pointer(_from_pointer(target.pointer)[:3])  # "operation": /paths/<path>/<method>
    fragment = _dump(_resolve(doc, _from_pointer(scope)))

    proposal = propose(SYSTEM_PROMPT, _user_prompt(target, scope, fragment, _guidance(settings)))
    cost = _cost(proposal, settings)
    if proposal.output.needs_human_input:
        return SkeletonResult(target.fingerprint, proposal, False, [], None, cost)

    for op in proposal.output.ops:
        _check_scope(op, scope)
    for op in proposal.output.ops:
        _apply(doc, op)
    patched_text = _dump(doc)

    after = _lint(patched_text, settings)
    before_fps = {v.fingerprint for v in before}
    introduced = [
        v.fingerprint for v in after if v.severity <= 1 and v.fingerprint not in before_fps
    ]
    resolved = target.fingerprint not in {v.fingerprint for v in after} and not introduced
    return SkeletonResult(target.fingerprint, proposal, resolved, introduced, patched_text, cost)


def bedrock_proposer(settings: Settings) -> Proposer:
    """Build a proposer backed by Bedrock Converse. Fails fast if the model is not configured."""
    if not settings.model_id or not settings.aws_region:
        raise ConfigError("GOVAGENT_MODEL_ID and GOVAGENT_AWS_REGION must be set to call Bedrock")

    from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
        BotoCoreError,
        ClientError,
    )
    from langchain_aws import ChatBedrockConverse

    llm = ChatBedrockConverse(
        model=settings.model_id,
        region_name=settings.aws_region,
        temperature=0,
        max_tokens=1024,
    )
    structured = llm.with_structured_output(LLMFixOutput, include_raw=True)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def propose(system: str, user: str) -> Proposal:
        messages = [("system", system), ("human", user)]
        try:
            response = cast(dict[str, Any], structured.invoke(messages))
        except (ClientError, BotoCoreError) as exc:
            raise LLMError(f"Bedrock call failed: {exc}") from exc
        if response["parsing_error"] is not None:
            raise PatchError(f"model output did not match the schema: {response['parsing_error']}")
        usage = cast(dict[str, int], response["raw"].usage_metadata or {})
        return Proposal(
            output=cast(LLMFixOutput, response["parsed"]),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    return propose
