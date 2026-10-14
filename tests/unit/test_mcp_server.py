"""MCP server over HTTP (JSON-RPC on /mcp), with the scripted model and pure-Python linter."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from govagent.agent.nodes import AgentDeps
from govagent.config import Settings
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.errors import ConfigError
from govagent.interfaces.mcp_server import build_app
from tests.unit.agent_fakes import FakeGitHost, FakeLinter, ScriptedModel, fix

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)
TOKEN = "test-key"  # noqa: S105
PROTOCOL = "2025-06-18"
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
ADD_SUMMARY = fix({"op": "add", "path": "/paths/~1pets/post/summary", "value": "Create a pet"})


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"mcp_api_key": SecretStr(TOKEN), **overrides}
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


REPO = "octo/demo"


@pytest.fixture
def github() -> FakeGitHost:
    return FakeGitHost({(REPO, "openapi.yaml"): SPEC})


@pytest.fixture
def client(clean_env: pytest.MonkeyPatch, github: FakeGitHost) -> Iterator[TestClient]:
    deps = AgentDeps(FakeLinter(), ScriptedModel(ADD_SUMMARY), RULES_META)
    app = build_app(
        settings(repo_allowlist=[REPO]), linter=FakeLinter(), agent_deps=deps, git_host=github
    )
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def rpc(client: TestClient, method: str, params: dict[str, Any], token: str = TOKEN) -> Any:
    response = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    return response


def call_tool(client: TestClient, name: str, **arguments: Any) -> dict[str, Any]:
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_health_check_needs_no_token(client: TestClient) -> None:
    response = client.get("/healthz")

    assert (response.status_code, response.text) == (200, "ok")


@pytest.mark.parametrize("token", ["", "wrong-key"])
def test_mcp_endpoint_rejects_missing_or_wrong_token(client: TestClient, token: str) -> None:
    response = rpc(client, "tools/list", {}, token=token)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_tools_are_listed(client: TestClient) -> None:
    tools = rpc(client, "tools/list", {}).json()["result"]["tools"]

    assert {tool["name"] for tool in tools} == {
        "lint_spec",
        "propose_fixes",
        "apply_fixes",
        "open_pull_request",
    }


def test_lint_spec(client: TestClient) -> None:
    result = call_tool(client, "lint_spec", spec_content=SPEC)

    assert result["structuredContent"]["count"] == 1
    [violation] = result["structuredContent"]["violations"]
    assert violation["rule_id"] == "gov-operation-summary"


def test_invalid_spec_is_a_tool_error(client: TestClient) -> None:
    result = call_tool(client, "lint_spec", spec_content="swagger: '2.0'\n")

    assert result["isError"] is True
    assert "3.0.x and 3.1.x" in result["content"][0]["text"]


def test_propose_then_apply_fixes_end_to_end(client: TestClient) -> None:
    proposed = call_tool(client, "propose_fixes", spec_content=SPEC)["structuredContent"]

    [proposal] = proposed["proposals"]
    assert proposal["rule_id"] == "gov-operation-summary"
    assert proposal["breaking"] is False
    assert "+      summary: Create a pet" in proposal["diff"]
    assert proposed["summary"]["violations_after_all_fixes"] == 0

    applied = call_tool(
        client,
        "apply_fixes",
        analysis_id=proposed["analysis_id"],
        approved_fix_ids=[proposal["id"]],
    )["structuredContent"]

    assert applied["applied"] == [proposal["id"]]
    assert applied["remaining_violations"] == []
    assert "      summary: Create a pet\n" in applied["spec_content"]


def test_unknown_analysis_is_a_tool_error(client: TestClient) -> None:
    result = call_tool(client, "apply_fixes", analysis_id="nope", approved_fix_ids=[])

    assert result["isError"] is True
    assert "propose_fixes again" in result["content"][0]["text"]


def test_rate_limit_per_key(clean_env: pytest.MonkeyPatch) -> None:
    clock = Clock()
    app = build_app(settings(mcp_rate_limit_per_minute=2), linter=FakeLinter(), clock=clock)
    with TestClient(app, base_url="http://localhost") as test_client:
        codes = [rpc(test_client, "tools/list", {}).status_code for _ in range(3)]
        clock.now += 61
        after_a_minute = rpc(test_client, "tools/list", {}).status_code

    assert codes == [200, 200, 429]
    assert after_a_minute == 200


def test_unknown_host_header_is_refused(client: TestClient) -> None:
    response = client.post(
        "http://evil.example.com/mcp",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 421  # DNS rebinding protection


def test_server_refuses_to_start_without_an_api_key(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="GOVAGENT_MCP_API_KEY"):
        build_app(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]


def test_lint_a_spec_read_from_github(client: TestClient, github: FakeGitHost) -> None:
    result = call_tool(client, "lint_spec", repo=REPO, path="openapi.yaml")

    assert result["structuredContent"]["count"] == 1
    assert github.calls == [("get_file", REPO, "openapi.yaml", "main")]


def test_repository_outside_the_allowlist_is_refused(client: TestClient) -> None:
    result = call_tool(client, "lint_spec", repo="octo/other", path="openapi.yaml")

    assert result["isError"] is True
    assert "GOVAGENT_REPO_ALLOWLIST" in result["content"][0]["text"]


def test_spec_source_must_be_text_or_repository(client: TestClient) -> None:
    result = call_tool(client, "lint_spec", spec_content=SPEC, repo=REPO, path="openapi.yaml")

    assert result["isError"] is True
    assert "either spec_content, or repo and path" in result["content"][0]["text"]


def test_propose_from_github_then_open_a_pull_request(
    client: TestClient, github: FakeGitHost
) -> None:
    proposed = call_tool(client, "propose_fixes", repo=REPO, path="openapi.yaml")
    proposal = proposed["structuredContent"]["proposals"][0]

    result = call_tool(
        client,
        "open_pull_request",
        analysis_id=proposed["structuredContent"]["analysis_id"],
        approved_fix_ids=[proposal["id"]],
        repo=REPO,
        path="openapi.yaml",
    )["structuredContent"]

    assert result["pr_url"] == "https://github.com/octo/demo/pull/1"
    assert result["applied"] == [proposal["id"]]
    assert "      summary: Create a pet\n" in github.commits[result["branch"]]
