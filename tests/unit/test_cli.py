"""CLI with the pure-Python linter and the scripted model wired in (no Spectral, no Bedrock)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from govagent.agent.nodes import AgentDeps
from govagent.agent.state import Budget
from govagent.config import Settings
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.ports import GitHost, Linter
from govagent.interfaces import wiring
from govagent.interfaces.cli import app
from tests.unit.agent_fakes import FakeGitHost, FakeLinter, ScriptedModel, fix

runner = CliRunner()
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
# FakeLinter order: camelCase id on get (breaking), then summary on post (non-breaking).
CAMEL = fix({"op": "replace", "path": "/paths/~1pets/get/operationId", "value": "listPets"})
SUMMARY = fix({"op": "add", "path": "/paths/~1pets/post/summary", "value": "Create a pet"})


@pytest.fixture
def spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def linter(settings: Settings) -> Linter:
        return FakeLinter()

    def agent_deps(settings: Settings) -> AgentDeps:
        return AgentDeps(FakeLinter(), ScriptedModel(CAMEL, SUMMARY), RULES_META)

    def budget(settings: Settings) -> Budget:
        return Budget(max_attempts_per_group=3, max_llm_calls_per_run=10, max_cost_usd_per_run=1)

    monkeypatch.setattr(wiring, "linter", linter)
    monkeypatch.setattr(wiring, "agent_deps", agent_deps)
    monkeypatch.setattr(wiring, "budget", budget)
    path = tmp_path / "openapi.yaml"
    path.write_text(SPEC)
    return path


def test_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_lint_table_exits_1_on_violations(spec: Path) -> None:
    result = runner.invoke(app, ["lint", str(spec)])

    assert result.exit_code == 1
    assert "gov-operation-id-camel" in result.stdout
    assert "2 violation(s)" in result.stdout


def test_lint_json(spec: Path) -> None:
    result = runner.invoke(app, ["lint", str(spec), "--format", "json"])

    rules = [v["rule_id"] for v in json.loads(result.stdout)]
    assert rules == ["gov-operation-id-camel", "gov-operation-summary"]


def test_lint_clean_spec_exits_0(spec: Path) -> None:
    clean = SPEC.replace("list_pets", "listPets").replace(
        "    post:\n", "    post:\n      summary: Create a pet\n"
    )
    spec.write_text(clean)

    result = runner.invoke(app, ["lint", str(spec)])

    assert result.exit_code == 0
    assert "no violation" in result.stdout


def test_fix_without_approval_flag_only_shows_the_diffs(spec: Path) -> None:
    result = runner.invoke(app, ["fix", str(spec)])

    assert result.exit_code == 0
    assert "BREAKING" in result.stdout
    assert "+      summary: Create a pet" in result.stdout
    assert "nothing applied" in result.stdout
    assert spec.read_text() == SPEC


def test_yes_non_breaking_applies_in_place_and_skips_breaking(spec: Path) -> None:
    result = runner.invoke(app, ["fix", str(spec), "--yes-non-breaking"])

    assert result.exit_code == 0, result.output
    assert "skipped (breaking, needs explicit approval)" in result.stdout
    fixed = spec.read_text()
    assert "      summary: Create a pet\n" in fixed
    assert "operationId: list_pets" in fixed  # the breaking rename was not applied


def test_interactive_applies_only_what_the_user_accepts(spec: Path, tmp_path: Path) -> None:
    out = tmp_path / "fixed.yaml"

    result = runner.invoke(
        app, ["fix", str(spec), "--interactive", "--out", str(out)], input="y\nn\n"
    )

    assert result.exit_code == 0, result.output
    assert "applied 1 fix(es)" in result.stdout
    assert "operationId: listPets" in out.read_text()
    assert "summary: Create a pet" not in out.read_text()
    assert spec.read_text() == SPEC  # --out leaves the input untouched


def test_dry_run_writes_nothing(spec: Path) -> None:
    result = runner.invoke(app, ["fix", str(spec), "--yes-non-breaking", "--dry-run"])

    assert result.exit_code == 0
    assert "dry run: would apply 1 fix(es)" in result.stdout
    assert spec.read_text() == SPEC


def test_interactive_and_yes_non_breaking_are_exclusive(spec: Path) -> None:
    result = runner.invoke(app, ["fix", str(spec), "--interactive", "--yes-non-breaking"])

    assert result.exit_code == 2


def test_fix_refuses_to_run_without_model_config(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean_env.chdir(tmp_path)  # no .env file here
    path = tmp_path / "openapi.yaml"
    path.write_text(SPEC)

    result = runner.invoke(app, ["fix", str(path)])

    assert result.exit_code == 1
    assert "GOVAGENT_MODEL_ID" in result.stderr


REPO = "octo/demo"


@pytest.fixture
def github(spec: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHost:
    host = FakeGitHost({(REPO, "openapi.yaml"): SPEC})

    def git_host(settings: Settings) -> GitHost:
        return host

    monkeypatch.setattr(wiring, "git_host", git_host)
    monkeypatch.setenv("GOVAGENT_REPO_ALLOWLIST", REPO)
    return host


def test_pr_opens_a_pull_request_with_non_breaking_fixes(github: FakeGitHost) -> None:
    args = ["pr", "--repo", REPO, "--path", "openapi.yaml", "--yes-non-breaking"]

    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "pull request opened: https://github.com/octo/demo/pull/1 (1 fix(es))" in result.stdout
    [committed] = github.commits.values()
    assert "      summary: Create a pet\n" in committed


def test_pr_refuses_a_repository_outside_the_allowlist(github: FakeGitHost) -> None:
    args = ["pr", "--repo", "octo/other", "--path", "openapi.yaml", "--yes-non-breaking"]

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "not in GOVAGENT_REPO_ALLOWLIST" in result.stderr
    assert github.calls == []
