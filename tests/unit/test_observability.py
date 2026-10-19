"""Structured logs: JSON lines, run_id on every line of a run, nothing sensitive."""

import io
import json
from pathlib import Path
from typing import Any

from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import analyze
from govagent.agent.state import Budget
from govagent.core.rules_meta import parse_rules_meta
from govagent.observability import configure_logging
from tests.unit.agent_fakes import FakeLinter, ScriptedModel, fix

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)
SPEC = """\
openapi: 3.0.3
info:
  title: Pets
  version: 1.0.0
  contact:
    email: api@example.com
paths:
  /pets:
    post:
      operationId: createPet
      responses:
        "201":
          description: Created
"""
WRONG = fix({"op": "add", "path": "/paths/~1pets/post/x-note", "value": "hi"})
RIGHT = fix({"op": "add", "path": "/paths/~1pets/post/summary", "value": "Create a pet"})
BUDGET = Budget(max_attempts_per_group=3, max_llm_calls_per_run=10, max_cost_usd_per_run=1)


def run_and_capture(level: str) -> tuple[str, list[dict[str, Any]]]:
    stream = io.StringIO()
    configure_logging(level, stream)
    report = analyze(SPEC, AgentDeps(FakeLinter(), ScriptedModel(WRONG, RIGHT), RULES_META), BUDGET)
    return report.analysis_id, [json.loads(line) for line in stream.getvalue().splitlines()]


def test_every_line_of_a_run_carries_the_report_id() -> None:
    analysis_id, lines = run_and_capture("INFO")

    assert lines
    assert {line["run_id"] for line in lines} == {analysis_id}
    assert all({"event", "level", "timestamp"} <= line.keys() for line in lines)


def test_the_story_of_a_run_is_in_the_events() -> None:
    _, lines = run_and_capture("INFO")

    events = [line["event"] for line in lines]
    assert events == [
        "analysis_started",
        "group_started",
        "proposal_received",
        "attempt_failed",
        "proposal_received",
        "group_settled",
        "analysis_finished",
    ]
    finished = lines[-1]
    assert finished["outcomes"] == {"resolved": 1}
    assert finished["llm_calls"] == 2


def test_prompts_and_spec_content_are_not_logged() -> None:
    _, lines = run_and_capture("INFO")

    text = json.dumps(lines)
    assert "Rule: gov-operation-summary" not in text
    assert "createPet" not in text


def test_warning_level_keeps_a_normal_run_silent() -> None:
    _, lines = run_and_capture("WARNING")

    assert lines == []
