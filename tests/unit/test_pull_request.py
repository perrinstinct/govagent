"""Opening a fix PR through the GitHost port (in-memory GitHub, pure-Python linter)."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import analyze
from govagent.agent.state import Budget
from govagent.core.pull_request import PullRequest, check_repo_allowed, open_fix_pull_request
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.errors import ApprovalError, RepoNotAllowedError, StaleSpecError
from govagent.domain.models import AnalysisReport
from tests.unit.agent_fakes import FakeGitHost, FakeLinter, ScriptedModel, fix

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)
REPO, PATH = "octo/demo", "api/openapi.yaml"
SPEC = """\
openapi: 3.0.3
info:
  title: Pets
  version: 1.0.0
paths:
  /pets:
    get:
      summary: List pets
      operationId: list_pets
      responses:
        "200":
          description: OK
    post:
      operationId: createPet
      responses:
        "201":
          description: Created
"""
CAMEL = fix({"op": "replace", "path": "/paths/~1pets/get/operationId", "value": "listPets"})
SUMMARY = fix(
    {"op": "add", "path": "/paths/~1pets/post/summary", "value": "Create a pet"},
    rationale="Adds a short imperative summary.",
)


@pytest.fixture(scope="module")
def report() -> AnalysisReport:
    model = ScriptedModel(CAMEL, SUMMARY)
    budget = Budget(max_attempts_per_group=3, max_llm_calls_per_run=10, max_cost_usd_per_run=1)
    return analyze(SPEC, AgentDeps(FakeLinter(), model, RULES_META), budget)


def ids(report: AnalysisReport) -> list[str]:
    return [o.proposal.id for o in report.outcomes if o.proposal is not None]


def open_pr(
    report: AnalysisReport,
    host: FakeGitHost,
    approved: list[str],
    allowlist: Sequence[str] = (REPO,),
) -> PullRequest:
    return open_fix_pull_request(
        host,
        repo=REPO,
        path=PATH,
        base="main",
        original_text=SPEC,
        report=report,
        approved_ids=approved,
        linter=FakeLinter(),
        allowlist=allowlist,
    )


def test_opens_a_pr_with_only_the_approved_fixes(report: AnalysisReport) -> None:
    host = FakeGitHost({(REPO, PATH): SPEC})
    _, summary = ids(report)

    pr = open_pr(report, host, [summary])

    branch = f"govagent/{report.analysis_id[:8]}"
    assert [call[0] for call in host.calls] == [
        "get_file",
        "create_branch",
        "update_file",
        "open_pr",
    ]
    assert host.calls[2][3:] == (
        branch,
        f"sha-{len(SPEC)}",
        f"Fix 1 API governance violation in {PATH}",
    )
    committed = host.commits[branch]
    assert "      summary: Create a pet\n" in committed
    assert "operationId: list_pets" in committed  # the breaking fix was not approved
    assert pr.url == "https://github.com/octo/demo/pull/1"
    assert pr.applied == [summary]


def test_pr_body_explains_each_fix_and_what_is_left(report: AnalysisReport) -> None:
    host = FakeGitHost({(REPO, PATH): SPEC})
    camel, summary = ids(report)

    open_pr(report, host, [camel, summary])

    body = host.pull_requests[0]["body"]
    assert "| Fixes in this PR | 2 (1 breaking) |" in body
    assert "### `gov-operation-summary` at `/paths/~1pets/post`" in body
    assert "Adds a short imperative summary." in body
    assert "> **Breaking change**" in body
    assert '"path": "/paths/~1pets/post/summary"' in body
    assert "| Violations left after this PR | 1 |" in body
    assert "## Needs human input\n\n- `gov-info-contact` at `/info`" in body


def test_repository_outside_the_allowlist_is_refused_before_any_call(
    report: AnalysisReport,
) -> None:
    host = FakeGitHost({(REPO, PATH): SPEC})

    with pytest.raises(RepoNotAllowedError, match="GOVAGENT_REPO_ALLOWLIST"):
        open_pr(report, host, ids(report), allowlist=["octo/other"])
    assert host.calls == []


def test_changed_file_is_refused(report: AnalysisReport) -> None:
    host = FakeGitHost({(REPO, PATH): SPEC + "x-changed: true\n"})

    with pytest.raises(StaleSpecError, match="changed since it was analyzed"):
        open_pr(report, host, ids(report))
    assert [call[0] for call in host.calls] == ["get_file"]


def test_empty_approval_opens_nothing(report: AnalysisReport) -> None:
    host = FakeGitHost({(REPO, PATH): SPEC})

    with pytest.raises(ApprovalError, match="no fix approved"):
        open_pr(report, host, [])
    assert [call[0] for call in host.calls] == ["get_file"]


def test_allowlist_is_case_insensitive() -> None:
    check_repo_allowed("Octo/Demo", ["octo/demo"])
