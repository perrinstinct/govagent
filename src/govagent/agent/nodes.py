"""Graph nodes and routing (docs/SPEC.md §6). Each node is `state -> partial update`.

Routing functions only read the state. Dependencies are injected through `AgentDeps`; the
LangGraph wiring lives in graph.py.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from govagent.agent.prompts import SYSTEM_PROMPT, render_ops, render_user_prompt
from govagent.agent.state import AgentState, Failure
from govagent.core.fragments import render_fragment
from govagent.core.grouping import group_violations, needs_human
from govagent.core.integrity import dangling_required
from govagent.core.patching import apply_patch
from govagent.core.pointers import normalize_scope
from govagent.core.scope_guard import scope_violations
from govagent.core.spec_io import SpecDocument, dump_spec, load_spec, resolve
from govagent.core.verification import verify
from govagent.domain.errors import InvalidSpecError, ModelOutputError, PatchError, PointerError
from govagent.domain.models import (
    FixOutcome,
    FixProposal,
    FixRequest,
    FixStatus,
    RuleMeta,
    Usage,
    Violation,
    ViolationGroup,
)
from govagent.domain.ports import FixModel, Linter

Update = dict[str, Any]


@dataclass(frozen=True)
class AgentDeps:
    linter: Linter
    fix_model: FixModel
    rules_meta: Mapping[str, RuleMeta]


def route_after_attempt_step(state: AgentState) -> Literal["next", "record", "retry"]:
    """After propose / guard / apply_verify: settled -> record, failed -> retry or give up."""
    if state.status is not None:
        return "record"
    if state.failure is None:
        return "next"
    if state.attempt < state.budget.max_attempts_per_group:
        return "retry"
    return "record"  # attempts exhausted: `record` turns the failure into a status


class Nodes:
    def __init__(self, deps: AgentDeps) -> None:
        self._linter = deps.linter
        self._model = deps.fix_model
        self._meta = deps.rules_meta

    # --- setup -------------------------------------------------------------------------------

    def lint(self, state: AgentState) -> Update:
        violations = self._linter.lint(state.spec_text)
        return {"violations": violations, "initial_violations": violations}

    def group(self, state: AgentState) -> Update:
        return {"groups": group_violations(state.violations, self._meta)}

    def next_group(self, state: AgentState) -> Update:
        current = next((g for g in state.groups if g.id not in state.processed), None)
        status = None
        if current is not None and needs_human(current, self._meta):
            status = FixStatus.NEEDS_HUMAN  # deterministic: no LLM call for this rule
        elif current is not None and state.budget_exhausted:
            status = FixStatus.BUDGET_EXCEEDED
        return {**_RESET, "current_group": current, "status": status}

    def route_next_group(self, state: AgentState) -> Literal["finalize", "record", "propose"]:
        if state.current_group is None:
            return "finalize"
        return "record" if state.status is not None else "propose"

    # --- one attempt -------------------------------------------------------------------------

    def propose(self, state: AgentState) -> Update:
        group = _current(state)
        if _budget_exhausted(state):
            return {"status": FixStatus.BUDGET_EXCEEDED, "budget_exhausted": True}

        attempt = state.attempt + 1
        meta = self._meta[group.rule_id]
        request = FixRequest(
            group_id=group.id,
            attempt=attempt,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=self._user_prompt(state, group, meta),
        )
        reset: Update = {**_ATTEMPT_RESET, "attempt": attempt}
        try:
            output, usage = self._model.propose(request)
        except ModelOutputError as exc:
            return {
                **reset,
                "usage": _add(state.usage, exc.usage),
                "failure": "schema",
                "last_feedback": f"Your answer did not match the expected schema: {exc}",
            }

        update: Update = {**reset, "usage": _add(state.usage, usage)}
        if output.needs_human_input:  # the model may downgrade a fix, never upgrade it
            return {**update, "status": FixStatus.NEEDS_HUMAN}
        proposal = FixProposal(
            id=f"{group.id}-{attempt}",
            group_id=group.id,
            ops=output.ops,
            rationale=output.rationale,
            breaking=meta.breaking,  # from rule metadata, never from the model
            attempt=attempt,
        )
        return {**update, "candidate": proposal}

    def guard(self, state: AgentState) -> Update:
        group, proposal = _current(state), _candidate(state)
        meta = self._meta[group.rule_id]
        problems = scope_violations(proposal.ops, group.scope_pointer, meta.extra_write_scopes)
        if problems:
            return _failed(state, "scope", "Operations outside the allowed scopes:", problems)
        return {}

    def apply_verify(self, state: AgentState) -> Update:
        group, proposal = _current(state), _candidate(state)
        spec = load_spec(state.spec_text, validate_openapi=False)
        try:
            patched_root = apply_patch(spec.root, proposal.ops)
        except (PatchError, PointerError) as exc:
            return _failed(state, "patch", "The patch could not be applied:", [str(exc)])
        dangling = dangling_required(patched_root) - dangling_required(spec.root)
        if dangling:
            details = [
                f"{pointer}/required lists {name!r}, which is not in its properties"
                for pointer, name in sorted(dangling)
            ]
            return _failed(state, "invalid_spec", "The patch breaks the document:", details)
        patched_text = dump_spec(SpecDocument(patched_root, spec.style))
        try:
            load_spec(patched_text)  # the result must still be a valid OpenAPI document
            after = self._linter.lint(patched_text)
        except InvalidSpecError as exc:
            return _failed(state, "invalid_spec", "The patched document is invalid:", [str(exc)])

        targets = {v.fingerprint for v in group.violations}
        result = verify(state.violations, after, targets, proposal.ops)
        if result.resolved:
            return {
                "status": FixStatus.RESOLVED,
                "candidate_text": patched_text,
                "candidate_violations": after,
            }
        details = [f"still present: {_describe(v)}" for v in result.remaining]
        details += [f"introduced: {_describe(v)}" for v in result.introduced]
        update = _failed(state, "not_resolved", "Re-linting the patched document found:", details)
        return {**update, "introduced": result.introduced}

    # --- bookkeeping -------------------------------------------------------------------------

    def record(self, state: AgentState) -> Update:
        group = _current(state)
        status = state.status
        if status is None:  # attempts exhausted
            status = FixStatus.REJECTED_SCOPE if state.failure == "scope" else FixStatus.FAILED
        keep_proposal = status in (FixStatus.RESOLVED, FixStatus.FAILED, FixStatus.REJECTED_SCOPE)
        outcome = FixOutcome(
            group_id=group.id,
            rule_id=group.rule_id,
            scope_pointer=group.scope_pointer,
            status=status,
            proposal=state.candidate if keep_proposal else None,
            attempts=state.attempt,
            introduced=state.introduced if status is not FixStatus.RESOLVED else [],
        )
        update: Update = {
            "outcomes": [*state.outcomes, outcome],
            "processed": [*state.processed, group.id],
        }
        if status is FixStatus.RESOLVED:
            # The fix becomes the base for the next groups; pointers may have moved, so regroup.
            violations = state.candidate_violations
            update |= {
                "spec_text": state.candidate_text,
                "violations": violations,
                "groups": group_violations(violations, self._meta),
            }
        return update

    def finalize(self, state: AgentState) -> Update:
        return {"current_group": None}

    # --- helpers -----------------------------------------------------------------------------

    def _user_prompt(self, state: AgentState, group: ViolationGroup, meta: RuleMeta) -> str:
        root = load_spec(state.spec_text, validate_openapi=False).root
        allowed = [group.scope_pointer, *(normalize_scope(s) for s in meta.extra_write_scopes)]
        context: list[tuple[str, str]] = []
        for pointer in allowed[1:]:
            try:
                resolve(root, pointer)
            except PointerError:
                context.append((pointer, "(does not exist yet)\n"))
            else:
                context.append((pointer, render_fragment(root, pointer, 2, 2000)))
        fragment = render_fragment(root, group.scope_pointer)
        return render_user_prompt(group, meta, allowed, fragment, context, state.last_feedback)


# Fields cleared when a new group starts, and when a new attempt starts.
_ATTEMPT_RESET: Update = {
    "candidate": None,
    "candidate_text": None,
    "candidate_violations": [],
    "introduced": [],
    "failure": None,
}
_RESET: Update = {**_ATTEMPT_RESET, "attempt": 0, "last_feedback": None}


def _failed(state: AgentState, failure: Failure, title: str, details: list[str]) -> Update:
    proposal = _candidate(state)
    feedback = "\n".join(
        [f"Previous operations: {render_ops(proposal.ops)}", title, *(f"- {d}" for d in details)]
    )
    return {"failure": failure, "last_feedback": feedback}


def _budget_exhausted(state: AgentState) -> bool:
    """Checked before each call: the last call may overshoot the cost cap by one call."""
    usage, budget = state.usage, state.budget
    return (
        usage.llm_calls >= budget.max_llm_calls_per_run
        or usage.cost_usd >= budget.max_cost_usd_per_run
    )


def _add(total: Usage, call: Usage) -> Usage:
    return Usage(
        llm_calls=total.llm_calls + call.llm_calls,
        input_tokens=total.input_tokens + call.input_tokens,
        output_tokens=total.output_tokens + call.output_tokens,
        cost_usd=total.cost_usd + call.cost_usd,
    )


def _describe(violation: Violation) -> str:
    return f"{violation.rule_id} at {violation.pointer or '/'}: {violation.message}"


def _current(state: AgentState) -> ViolationGroup:
    if state.current_group is None:
        raise RuntimeError("no current group")  # graph wiring bug, not a user error
    return state.current_group


def _candidate(state: AgentState) -> FixProposal:
    if state.candidate is None:
        raise RuntimeError("no candidate proposal")  # graph wiring bug, not a user error
    return state.candidate
