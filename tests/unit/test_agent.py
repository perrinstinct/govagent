"""Agent graph scenarios: scripted model, pure-Python linter (no network, no Spectral)."""

from pathlib import Path

import pytest

from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import analyze, run_agent
from govagent.agent.state import Budget
from govagent.core.approval import auto_approvable_ids
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.models import AnalysisReport, FixStatus
from tests.unit.agent_fakes import FakeLinter, ScriptedModel, fix, human, schema_error

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)
BUDGET = Budget(max_attempts_per_group=3, max_llm_calls_per_run=40, max_cost_usd_per_run=0.5)

SPEC = """\
openapi: 3.0.3
info:
  title: Pets
  version: 1.0.0
  contact:
    email: api@example.com
paths:
  /pets:
    get:
      summary: List pets
      operationId: listPets
      responses:
        "200":
          description: OK
    post:
      operationId: createPet # used by generated clients
      responses:
        "201":
          description: Created
"""
POST = "/paths/~1pets/post"
ADD_SUMMARY = {"op": "add", "path": f"{POST}/summary", "value": "Create a pet"}


def run(model: ScriptedModel, spec: str = SPEC, budget: Budget = BUDGET) -> AnalysisReport:
    return analyze(spec, AgentDeps(FakeLinter(), model, RULES_META), budget)


def statuses(report: AnalysisReport) -> list[FixStatus]:
    return [outcome.status for outcome in report.outcomes]


# --- acceptance scenarios (docs/SPEC.md §9, M3) ------------------------------------------------


def test_resolved_on_first_try() -> None:
    model = ScriptedModel(fix(ADD_SUMMARY))

    report = run(model)

    [outcome] = report.outcomes
    assert outcome.status is FixStatus.RESOLVED
    assert outcome.attempts == 1
    assert outcome.proposal is not None
    assert outcome.proposal.breaking is False
    assert report.final_violations == []
    assert report.usage.llm_calls == 1


def test_resolved_on_retry_with_feedback() -> None:
    wrong = fix({"op": "add", "path": f"{POST}/x-note", "value": "hi"})
    model = ScriptedModel(wrong, fix(ADD_SUMMARY))

    report = run(model)

    assert statuses(report) == [FixStatus.RESOLVED]
    assert report.outcomes[0].attempts == 2
    retry_prompt = model.requests[1].user_prompt
    assert "Your previous attempt was rejected" in retry_prompt
    assert '"path": "/paths/~1pets/post/x-note"' in retry_prompt
    assert f"still present: gov-operation-summary at {POST}" in retry_prompt


def test_scope_rejection_is_a_failed_attempt_with_feedback() -> None:
    outside = fix(ADD_SUMMARY, {"op": "replace", "path": "/info/title", "value": "Hacked"})
    model = ScriptedModel(outside, fix(ADD_SUMMARY))

    report = run(model)

    assert statuses(report) == [FixStatus.RESOLVED]
    assert report.outcomes[0].attempts == 2
    assert "outside the allowed scopes" in model.requests[1].user_prompt
    assert "/info/title" in model.requests[1].user_prompt


def test_scope_rejected_on_every_attempt() -> None:
    outside = fix({"op": "replace", "path": "/info/title", "value": "Hacked"})
    model = ScriptedModel(outside, outside, outside)

    state = run_agent(SPEC, AgentDeps(FakeLinter(), model, RULES_META), BUDGET)

    [outcome] = state.outcomes
    assert outcome.status is FixStatus.REJECTED_SCOPE
    assert outcome.attempts == 3
    assert state.spec_text == SPEC  # nothing was applied


def test_failed_after_max_attempts() -> None:
    wrong = fix({"op": "add", "path": f"{POST}/x-note", "value": "hi"})
    model = ScriptedModel(wrong, wrong, wrong)

    report = run(model)

    [outcome] = report.outcomes
    assert outcome.status is FixStatus.FAILED
    assert outcome.attempts == 3
    assert outcome.proposal is not None
    assert outcome.proposal.attempt == 3  # last attempt kept for the report
    assert [v.rule_id for v in report.final_violations] == ["gov-operation-summary"]
    assert auto_approvable_ids(report) == []


def test_budget_exceeded_on_llm_calls() -> None:
    spec = SPEC.replace("      summary: List pets\n", "")  # two operations without summary
    model = ScriptedModel(fix({"op": "add", "path": "/paths/~1pets/get/summary", "value": "L"}))
    budget = BUDGET.model_copy(update={"max_llm_calls_per_run": 1})

    report = run(model, spec, budget)

    assert statuses(report) == [FixStatus.RESOLVED, FixStatus.BUDGET_EXCEEDED]
    assert report.outcomes[1].attempts == 0
    assert report.usage.llm_calls == 1


def test_budget_exceeded_on_cost_is_checked_before_each_call() -> None:
    spec = SPEC.replace("      summary: List pets\n", "").replace(
        "operationId: listPets", "operationId: list_pets"
    )
    model = ScriptedModel(
        fix({"op": "add", "path": "/paths/~1pets/get/summary", "value": "List pets"}),
        fix({"op": "replace", "path": "/paths/~1pets/get/operationId", "value": "listPets"}),
        cost_per_call=0.3,
    )

    report = run(model, spec)

    # 0.0 < 0.5 -> call; 0.3 < 0.5 -> call; 0.6 >= 0.5 -> stop (overshoot of at most one call)
    assert statuses(report) == [
        FixStatus.RESOLVED,
        FixStatus.RESOLVED,
        FixStatus.BUDGET_EXCEEDED,
    ]
    assert report.usage.cost_usd == pytest.approx(0.6)


def test_needs_human_rule_is_skipped_without_model_call() -> None:
    spec = SPEC.replace("  contact:\n    email: api@example.com\n", "")
    model = ScriptedModel(fix(ADD_SUMMARY))

    report = run(model, spec)

    assert statuses(report) == [FixStatus.NEEDS_HUMAN, FixStatus.RESOLVED]
    assert report.outcomes[0].attempts == 0
    assert report.outcomes[0].proposal is None
    assert [r.group_id for r in model.requests] == [report.outcomes[1].group_id]


def test_needs_human_rule_stays_needs_human_when_budget_is_exhausted() -> None:
    spec = SPEC.replace("  contact:\n    email: api@example.com\n", "")
    budget = BUDGET.model_copy(update={"max_llm_calls_per_run": 0})

    report = run(ScriptedModel(), spec, budget)

    assert statuses(report) == [FixStatus.NEEDS_HUMAN, FixStatus.BUDGET_EXCEEDED]


# --- other paths through the graph ---------------------------------------------------------


def test_model_may_downgrade_to_needs_human() -> None:
    report = run(ScriptedModel(human()))

    [outcome] = report.outcomes
    assert outcome.status is FixStatus.NEEDS_HUMAN
    assert outcome.attempts == 1
    assert outcome.proposal is None


def test_schema_error_is_paid_for_and_retried() -> None:
    model = ScriptedModel(schema_error(), fix(ADD_SUMMARY))

    report = run(model)

    assert statuses(report) == [FixStatus.RESOLVED]
    assert report.usage.llm_calls == 2
    assert "did not match the expected schema" in model.requests[1].user_prompt


def test_patch_error_is_retried() -> None:
    bad = fix({"op": "replace", "path": f"{POST}/summary", "value": "Create a pet"})
    model = ScriptedModel(bad, fix(ADD_SUMMARY))

    report = run(model)

    assert statuses(report) == [FixStatus.RESOLVED]
    assert "could not be applied" in model.requests[1].user_prompt


def test_fix_producing_invalid_openapi_is_rejected() -> None:
    breaks_spec = fix(ADD_SUMMARY, {"op": "replace", "path": f"{POST}/responses", "value": "x"})
    model = ScriptedModel(breaks_spec, fix(ADD_SUMMARY))

    report = run(model)

    assert statuses(report) == [FixStatus.RESOLVED]
    assert "patched document is invalid" in model.requests[1].user_prompt


def test_fix_creating_a_dangling_ref_is_a_failed_attempt_not_a_crash() -> None:
    dangling = {"$ref": "#/components/responses/Nope"}
    breaks_ref = fix(ADD_SUMMARY, {"op": "add", "path": f"{POST}/responses/400", "value": dangling})
    model = ScriptedModel(breaks_ref, fix(ADD_SUMMARY))

    report = run(model)

    assert statuses(report) == [FixStatus.RESOLVED]
    assert "unresolvable $ref" in model.requests[1].user_prompt


def test_prompt_contains_rule_guidance_scope_and_fragment() -> None:
    model = ScriptedModel(fix(ADD_SUMMARY))

    run(model)

    request = model.requests[0]
    assert request.attempt == 1
    assert "Rule: gov-operation-summary" in request.user_prompt
    assert RULES_META["gov-operation-summary"].guidance in request.user_prompt
    assert f"- {POST}" in request.user_prompt
    assert "operationId: createPet" in request.user_prompt
    assert "previous attempt" not in request.user_prompt


def test_fixes_apply_sequentially_and_groups_follow_moved_pointers() -> None:
    spec = SPEC.replace("  /pets:\n", "  /petStore:\n")  # kebab violation + summary below it
    rename = fix({"op": "move", "from": "/paths/~1petStore", "path": "/paths/~1pet-store"})
    summary = fix({"op": "add", "path": "/paths/~1pet-store/post/summary", "value": "Create a pet"})
    model = ScriptedModel(rename, summary)

    state = run_agent(spec, AgentDeps(FakeLinter(), model, RULES_META), BUDGET)

    assert [o.status for o in state.outcomes] == [FixStatus.RESOLVED, FixStatus.RESOLVED]
    assert state.violations == []
    # The second group was recomputed on the renamed path, after the first fix was applied.
    assert "/paths/~1pet-store/post" in model.requests[1].user_prompt
    assert "  /pet-store:\n" in state.spec_text
    assert "operationId: createPet # used by generated clients" in state.spec_text


def test_report_fields() -> None:
    report = run(ScriptedModel(fix(ADD_SUMMARY)))

    assert len(report.analysis_id) == 32
    assert len(report.spec_sha256) == 64
    assert [v.rule_id for v in report.initial_violations] == ["gov-operation-summary"]
    assert report.duration_ms >= 0


# --- invariant: breaking fixes are never auto-approved -------------------------------------


def test_breaking_fix_is_flagged_from_metadata_and_never_auto_approved() -> None:
    spec = SPEC.replace("operationId: listPets", "operationId: list_pets")
    model = ScriptedModel(
        fix({"op": "replace", "path": "/paths/~1pets/get/operationId", "value": "listPets"}),
        fix(ADD_SUMMARY),
    )

    report = run(model, spec)

    assert statuses(report) == [FixStatus.RESOLVED, FixStatus.RESOLVED]
    camel, summary = (o.proposal for o in report.outcomes)
    assert camel is not None
    assert summary is not None
    assert camel.breaking is True  # the model never said anything about breaking-ness
    assert auto_approvable_ids(report) == [summary.id]
