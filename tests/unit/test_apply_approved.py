"""Applying a subset of approved proposals (docs/SPEC.md §7), with the pure-Python linter."""

from pathlib import Path

import pytest

from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import analyze
from govagent.agent.state import Budget
from govagent.core.approval import (
    apply_approvable,
    apply_approved,
    auto_approvable_ids,
    proposal_diffs,
)
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.errors import ApprovalError
from govagent.domain.models import AnalysisReport
from tests.unit.agent_fakes import FakeLinter, ScriptedModel, fix

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)
BUDGET = Budget(max_attempts_per_group=3, max_llm_calls_per_run=10, max_cost_usd_per_run=1.0)

SPEC = """\
openapi: 3.0.3
info:
  title: Pets
  version: 1.0.0
  contact:
    email: api@example.com
paths:
  /petStore:
    get:
      summary: List pets
      operationId: list_pets # snake case on purpose
      responses:
        "200":
          description: OK
    post:
      operationId: createPet
      responses:
        "201":
          description: Created
"""
RENAME = {"op": "move", "from": "/paths/~1petStore", "path": "/paths/~1pet-store"}
CAMEL = {"op": "replace", "path": "/paths/~1pet-store/get/operationId", "value": "listPets"}
SUMMARY = {"op": "add", "path": "/paths/~1pet-store/post/summary", "value": "Create a pet"}


@pytest.fixture(scope="module")
def report() -> AnalysisReport:
    # FakeLinter order: path rename (breaking), camelCase id on get (breaking), summary on post.
    model = ScriptedModel(fix(RENAME), fix(CAMEL), fix(SUMMARY))
    report = analyze(SPEC, AgentDeps(FakeLinter(), model, RULES_META), BUDGET)
    assert [o.rule_id for o in report.outcomes] == [
        "gov-path-kebab",
        "gov-operation-id-camel",
        "gov-operation-summary",
    ]
    return report


def ids(report: AnalysisReport) -> list[str]:
    return [o.proposal.id for o in report.outcomes if o.proposal is not None]


def test_all_approved_reproduces_the_agent_result(report: AnalysisReport) -> None:
    result = apply_approved(SPEC, report, ids(report), FakeLinter())

    assert result.applied == ids(report)
    assert result.remaining == []
    assert "  /pet-store:\n" in result.spec_text
    assert "operationId: listPets # snake case on purpose" in result.spec_text


def test_subset_that_depends_on_an_unapproved_rename_is_rejected(report: AnalysisReport) -> None:
    rename, camel, summary = ids(report)

    # Every unapproved proposal applied before it is listed: the rename is the culprit here.
    message = f"{summary} no longer applies without the unapproved {rename}, {camel}"
    with pytest.raises(ApprovalError, match=message):
        apply_approved(SPEC, report, [summary], FakeLinter())


def test_independent_subset_applies_and_leaves_the_rest(report: AnalysisReport) -> None:
    rename, camel, summary = ids(report)

    result = apply_approved(SPEC, report, [rename, summary], FakeLinter())

    assert result.applied == [rename, summary]
    assert [v.rule_id for v in result.remaining] == ["gov-operation-id-camel"]
    assert camel not in result.applied


def test_non_breaking_selection_skips_breaking_fixes(report: AnalysisReport) -> None:
    _, _, summary = ids(report)

    assert auto_approvable_ids(report) == [summary]


def test_unknown_ids_are_rejected(report: AnalysisReport) -> None:
    with pytest.raises(ApprovalError, match="not a resolved proposal"):
        apply_approved(SPEC, report, ["nope-1"], FakeLinter())


def test_each_proposal_has_a_diff_on_top_of_the_previous_ones(report: AnalysisReport) -> None:
    rename, camel, summary = ids(report)

    diffs = proposal_diffs(SPEC, report)

    assert "-  /petStore:\n+  /pet-store:\n" in diffs[rename]
    assert "+      summary: Create a pet\n" in diffs[summary]
    assert "-      operationId: list_pets # snake case on purpose" in diffs[camel]
    assert "+      operationId: listPets # snake case on purpose" in diffs[camel]


def test_best_effort_drops_fixes_that_need_an_unapproved_one(report: AnalysisReport) -> None:
    *_, summary = ids(report)

    result, dropped = apply_approvable(SPEC, report, [summary], FakeLinter())

    assert dropped == [summary]
    assert result.applied == []
    assert result.spec_text == SPEC
