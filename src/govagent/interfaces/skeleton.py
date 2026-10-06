"""M1 walking skeleton: lint -> one LLM call -> patch -> re-lint, for a single rule.

Reachable only through `govagent fix --skeleton`. Since M2 it runs on the real deterministic
core (spec_io, fragments, patching, scope guard, verification) and the Spectral adapter; only
the LLM part below is still throwaway, replaced by agent/ and adapters/bedrock.py in M3.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, Field

from govagent.config import Settings
from govagent.core.fragments import render_fragment
from govagent.core.grouping import resolve_scope
from govagent.core.patching import apply_patch
from govagent.core.rules_meta import parse_rules_meta
from govagent.core.scope_guard import scope_violations
from govagent.core.spec_io import SpecDocument, dump_spec, load_spec
from govagent.core.verification import verify
from govagent.domain.errors import ConfigError, LLMError, PatchError, ScopeError
from govagent.domain.models import PatchOp, Violation
from govagent.domain.ports import Linter

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


def _user_prompt(target: Violation, scope: str, fragment: str, guidance: str) -> str:
    return (
        f"Rule: {target.rule_id}\n"
        f"Guidance: {guidance}\n"
        f"Violations:\n- {target.pointer}: {target.message}\n"
        f"Allowed scope pointer (write only under it): {scope}\n"
        f"Fragment at {scope} (YAML):\n{fragment}"
    )


def _cost(proposal: Proposal, settings: Settings) -> float | None:
    if settings.price_input_per_mtok is None or settings.price_output_per_mtok is None:
        return None
    return (
        proposal.input_tokens * settings.price_input_per_mtok
        + proposal.output_tokens * settings.price_output_per_mtok
    ) / 1_000_000


def run(spec_text: str, settings: Settings, linter: Linter, propose: Proposer) -> SkeletonResult:
    """Fix the first `gov-operation-summary` violation with one LLM call, then verify."""
    spec = load_spec(spec_text)
    before = linter.lint(spec_text)
    target = next((v for v in before if v.rule_id == RULE_ID), None)
    if target is None:
        return SkeletonResult(None, None, True, [], None, None)

    meta = parse_rules_meta(settings.rules_meta_path.read_text())[RULE_ID]
    scope = resolve_scope(meta, target.pointer)
    fragment = render_fragment(spec.root, scope)

    proposal = propose(SYSTEM_PROMPT, _user_prompt(target, scope, fragment, meta.guidance))
    cost = _cost(proposal, settings)
    if proposal.output.needs_human_input:
        return SkeletonResult(target.fingerprint, proposal, False, [], None, cost)

    problems = scope_violations(proposal.output.ops, scope, meta.extra_write_scopes)
    if problems:
        raise ScopeError("; ".join(problems))
    patched = SpecDocument(apply_patch(spec.root, proposal.output.ops), spec.style)
    patched_text = dump_spec(patched)

    result = verify(before, linter.lint(patched_text), {target.fingerprint})
    introduced = [v.fingerprint for v in result.introduced]
    return SkeletonResult(
        target.fingerprint, proposal, result.resolved, introduced, patched_text, cost
    )


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
