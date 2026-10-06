"""Graph state (docs/SPEC.md §6). Pydantic and serializable, so a run can be inspected."""

from typing import Literal

from pydantic import BaseModel

from govagent.domain.models import (
    FixOutcome,
    FixProposal,
    FixStatus,
    Usage,
    Violation,
    ViolationGroup,
)

# Why the current attempt failed; drives the retry feedback and the final status.
Failure = Literal["schema", "scope", "patch", "invalid_spec", "not_resolved"]


class Budget(BaseModel, frozen=True):
    max_attempts_per_group: int
    max_llm_calls_per_run: int
    max_cost_usd_per_run: float


class AgentState(BaseModel):
    budget: Budget
    spec_text: str  # working copy: every RESOLVED proposal is applied to it, in order
    violations: list[Violation] = []  # current lint of spec_text
    initial_violations: list[Violation] = []
    groups: list[ViolationGroup] = []  # queue, recomputed from `violations` after each fix
    processed: list[str] = []  # ids of groups that already have an outcome

    # Current group and attempt
    current_group: ViolationGroup | None = None
    attempt: int = 0
    candidate: FixProposal | None = None  # proposal of the current attempt
    candidate_text: str | None = None  # spec_text with the candidate applied
    candidate_violations: list[Violation] = []  # lint of candidate_text
    introduced: list[Violation] = []  # new violations caused by the candidate
    failure: Failure | None = None
    last_feedback: str | None = None  # sent to the model on the next attempt
    status: FixStatus | None = None  # set when the group is settled, consumed by `record`

    outcomes: list[FixOutcome] = []
    usage: Usage = Usage()
    budget_exhausted: bool = False
