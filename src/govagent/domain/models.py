"""Domain models (docs/SPEC.md §3). Pure data: no I/O, no project imports."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Severity(StrEnum):
    ERROR = "error"
    WARN = "warn"
    INFO = "info"
    HINT = "hint"


class FixClass(StrEnum):
    AUTO = "auto"  # agent may fix
    NEEDS_HUMAN = "needs_human_input"  # requires business knowledge; no LLM call


class Violation(BaseModel, frozen=True):
    rule_id: str
    severity: Severity
    pointer: str  # RFC 6901 JSON pointer
    message: str

    @property
    def fingerprint(self) -> str:
        return f"{self.rule_id}:{self.pointer}"


class RuleMeta(BaseModel, frozen=True):
    rule_id: str
    fix_class: FixClass
    breaking: bool
    scope: str  # literal pointer ("/servers") or template ("operation", "schema")
    extra_write_scopes: list[str] = []  # e.g. ["/components/schemas", "/components/responses"]
    guidance: str  # short fixing guidance injected in the prompt


class PatchOp(BaseModel, frozen=True, populate_by_name=True):
    op: Literal["add", "remove", "replace", "move"]
    path: str
    value: Any | None = None
    from_: str | None = Field(default=None, alias="from")


class ViolationGroup(BaseModel, frozen=True):
    id: str  # stable hash of rule_id + scope_pointer
    rule_id: str
    scope_pointer: str
    violations: list[Violation]


class FixProposal(BaseModel, frozen=True):
    id: str
    group_id: str
    ops: list[PatchOp]
    rationale: str  # 1-3 sentences, used in the PR body
    breaking: bool  # copied from RuleMeta, never from the LLM
    attempt: int


class FixStatus(StrEnum):
    RESOLVED = "resolved"
    FAILED = "failed"  # attempts exhausted
    REJECTED_SCOPE = "rejected_scope"  # ops outside allowed scopes (counts as a failed attempt)
    NEEDS_HUMAN = "needs_human_input"
    BUDGET_EXCEEDED = "budget_exceeded"


class FixOutcome(BaseModel, frozen=True):
    group_id: str
    rule_id: str
    scope_pointer: str
    status: FixStatus
    proposal: FixProposal | None
    attempts: int
    introduced: list[Violation] = []  # new violations caused by the last attempt


class Usage(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class AnalysisReport(BaseModel, frozen=True):
    analysis_id: str
    spec_sha256: str
    initial_violations: list[Violation]
    outcomes: list[FixOutcome]
    final_violations: list[Violation]  # after applying all RESOLVED proposals
    usage: Usage
    duration_ms: int


class LLMFixOutput(BaseModel, frozen=True):
    """Structured output requested from the model for one fix attempt."""

    ops: list[PatchOp] = Field(
        description="JSON Patch operations (add, remove, replace, move), in application order. "
        "Pointers are absolute from the document root."
    )
    rationale: str = Field(description="1-3 sentences explaining the fix, for the PR body.")
    needs_human_input: bool = Field(
        description="True when the fix requires business knowledge; ops must then be empty."
    )


class FixRequest(BaseModel, frozen=True):
    """One call to the fix model. Prompts are rendered by the agent (agent/prompts.py)."""

    group_id: str
    attempt: int
    system_prompt: str
    user_prompt: str
