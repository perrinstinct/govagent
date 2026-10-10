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
    results, skipped = evaluate([CASE], deps(ScriptedModel(ADD_SUMMARY)), BUDGET, 1.0)

    [result] = results
    assert skipped == 0
    assert result.sane
    assert result.final == []
    assert result.fixed_by_rule() == {"gov-operation-summary": (1, 1)}
    assert result.final_valid
    assert [o.status for o in result.outcomes] == [FixStatus.RESOLVED]
    assert result.usage.llm_calls == 1
    assert result.error is None


def test_global_cost_cap_stops_before_the_next_case() -> None:
    model = ScriptedModel(ADD_SUMMARY, ADD_SUMMARY, cost_per_call=0.6)

    results, skipped = evaluate([CASE, CASE, CASE], deps(model), BUDGET, 1.0)

    assert len(results) == 2  # 0.0 -> run, 0.6 -> run, 1.2 >= 1.0 -> stop
    assert skipped == 1


def test_provider_error_is_recorded_and_the_run_continues() -> None:
    def outage(request: FixRequest) -> LLMFixOutput:
        raise LLMError("Bedrock call failed: ThrottlingException")

    model = ScriptedModel(outage, ADD_SUMMARY)

    results, _ = evaluate([CASE, CASE], deps(model), BUDGET, 1.0)

    assert results[0].error == "LLMError: Bedrock call failed: ThrottlingException"
    assert results[0].fixed_by_rule() == {"gov-operation-summary": (1, 0)}
    assert results[1].error is None


def test_model_slug() -> None:
    assert model_slug("eu.anthropic.claude-haiku-4-5-20251001-v1:0") == (
        "claude-haiku-4-5-20251001-v1-0"
    )
