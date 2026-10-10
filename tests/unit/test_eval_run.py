"""The eval runner with the scripted model and the pure-Python linter (no network)."""

from pathlib import Path

from evals.build_dataset import Case, InjectedViolation
from evals.run import evaluate, model_slug
from govagent.agent.nodes import AgentDeps
from govagent.agent.state import Budget
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.errors import LLMError
from govagent.domain.models import FixRequest, FixStatus, LLMFixOutput
from tests.unit.agent_fakes import FakeLinter, ScriptedModel, fix

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)
BUDGET = Budget(max_attempts_per_group=3, max_llm_calls_per_run=10, max_cost_usd_per_run=1.0)
SPEC = """\
openapi: 3.0.3
info:
  title: t
  version: '1'
  contact:
    email: a@example.com
paths:
  /pets:
    get:
      operationId: listPets
      responses:
        '200':
          description: OK
"""
GET = "/paths/~1pets/get"
CASE = Case(
    id="pets--operation-summary",
    seed="pets.yaml",
    kind="single",
    injected=[InjectedViolation(rule_id="gov-operation-summary", pointer=GET)],
    spec_text=SPEC,
)
ADD_SUMMARY = fix({"op": "add", "path": f"{GET}/summary", "value": "List pets"})


def deps(model: ScriptedModel) -> AgentDeps:
    return AgentDeps(FakeLinter(), model, RULES_META)


def test_case_result_records_fix_usage_and_validity() -> None:
    evaluation = evaluate([CASE], deps(ScriptedModel(ADD_SUMMARY)), BUDGET, 1.0)

    [result] = evaluation.results
    assert evaluation.skipped == 0
    assert result.sane
    assert result.final == []
    assert result.fixed_by_rule() == {"gov-operation-summary": (1, 1)}
    assert result.final_valid
    assert [o.status for o in result.outcomes] == [FixStatus.RESOLVED]
    assert result.usage.llm_calls == 1
    assert result.error is None


def test_global_cost_cap_stops_before_the_next_case() -> None:
    model = ScriptedModel(ADD_SUMMARY, ADD_SUMMARY, cost_per_call=0.6)

    evaluation = evaluate([CASE, CASE, CASE], deps(model), BUDGET, 1.0)

    assert len(evaluation.results) == 2  # 0.0 -> run, 0.6 -> run, 1.2 >= 1.0 -> stop
    assert evaluation.skipped == 1


def test_provider_error_is_recorded_and_the_run_continues() -> None:
    def outage(request: FixRequest) -> LLMFixOutput:
        raise LLMError("Bedrock call failed: ThrottlingException")

    model = ScriptedModel(outage, ADD_SUMMARY)

    results = evaluate([CASE, CASE], deps(model), BUDGET, 1.0).results

    assert results[0].error == "LLMError: Bedrock call failed: ThrottlingException"
    assert results[0].fixed_by_rule() == {"gov-operation-summary": (1, 0)}
    assert results[1].error is None


def test_run_stops_after_three_errored_cases_in_a_row() -> None:
    def denied(request: FixRequest) -> LLMFixOutput:
        raise LLMError("Bedrock call failed: use case details have not been submitted")

    evaluation = evaluate([CASE] * 5, deps(ScriptedModel(*[denied] * 5)), BUDGET, 1.0)

    assert len(evaluation.results) == 3
    assert evaluation.skipped == 2
    assert evaluation.stopped_reason is not None
    assert "3 errored cases in a row" in evaluation.stopped_reason


def test_every_call_is_recorded_with_prompts_output_and_usage() -> None:
    wrong = fix({"op": "add", "path": f"{GET}/x-note", "value": "hi"})

    calls = evaluate([CASE], deps(ScriptedModel(wrong, ADD_SUMMARY)), BUDGET, 1.0).calls

    assert [(c.case_id, c.attempt) for c in calls] == [(CASE.id, 1), (CASE.id, 2)]
    assert "Rule: gov-operation-summary" in calls[0].user_prompt
    assert "Your previous attempt was rejected" in calls[1].user_prompt
    assert calls[1].output == ADD_SUMMARY
    assert calls[0].usage.llm_calls == 1


def test_paid_calls_of_a_failed_case_count_towards_its_cost_and_the_cap() -> None:
    wrong = fix({"op": "add", "path": f"{GET}/x-note", "value": "hi"})

    def outage(request: FixRequest) -> LLMFixOutput:
        raise LLMError("Bedrock call failed: ThrottlingException")

    model = ScriptedModel(wrong, outage, ADD_SUMMARY, cost_per_call=0.6)

    evaluation = evaluate([CASE, CASE], deps(model), BUDGET, 0.5)

    [failed] = evaluation.results  # the 0.6 already spent stops the run before case 2
    assert failed.error is not None
    assert failed.usage.cost_usd == 0.6
    assert evaluation.skipped == 1


def test_model_slug() -> None:
    assert model_slug("eu.anthropic.claude-haiku-4-5-20251001-v1:0") == (
        "claude-haiku-4-5-20251001-v1-0"
    )
