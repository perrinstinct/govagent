"""MCP server (docs/SPEC.md §8): streamable HTTP on /mcp, stateless, behind a Bearer token.

Tools: `lint_spec`, `propose_fixes` (runs the agent, stores the analysis), `apply_fixes`
(returns the spec with the approved fixes) and `open_pull_request` (opens a PR with them on an
allowlisted GitHub repository). Specs are passed as text or read from GitHub. The core is
synchronous: tools run it in a worker thread so the event loop stays free.
"""

import hmac
import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from govagent.agent.nodes import AgentDeps
from govagent.config import Settings
from govagent.core.approval import apply_approved, proposal_diffs
from govagent.core.pull_request import check_repo_allowed, open_fix_pull_request
from govagent.core.spec_io import load_spec
from govagent.domain.errors import ConfigError, GovagentError
from govagent.domain.models import AnalysisReport, FixStatus, Violation
from govagent.domain.ports import GitHost, Linter
from govagent.interfaces import wiring
from govagent.interfaces.analysis_store import AnalysisStore
from govagent.observability import get_logger

log = get_logger(__name__)

INSTRUCTIONS = """\
govagent checks OpenAPI 3.0/3.1 specs against API governance rules and proposes verified
JSON Patch fixes. Typical flow: lint_spec -> propose_fixes -> show the user each diff ->
apply_fixes (returns the fixed spec) or open_pull_request (GitHub) with the ids the user
approved. Fixes marked breaking change the API contract: only include them when the user
explicitly approves them. Never approve fixes on the user's behalf.
"""


def build_app(
    settings: Settings,
    *,
    linter: Linter | None = None,
    agent_deps: AgentDeps | None = None,
    git_host: GitHost | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Starlette:
    """The ASGI app. Real adapters are built lazily unless injected (tests pass fakes)."""
    if settings.mcp_api_key is None or not settings.mcp_api_key.get_secret_value():
        raise ConfigError("GOVAGENT_MCP_API_KEY must be set to run the MCP server")
    store = AnalysisStore(settings.analysis_ttl_seconds)
    tools = _Tools(settings, linter, agent_deps, git_host, store)
    server = MCPServer(name="govagent", instructions=INSTRUCTIONS)
    server.tool()(tools.lint_spec)
    server.tool()(tools.propose_fixes)
    server.tool()(tools.apply_fixes)
    server.tool()(tools.open_pull_request)

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        return PlainTextResponse("ok")

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.mcp_allowed_hosts,
        allowed_origins=[],
    )
    app = server.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security
    )
    app.add_middleware(
        BearerAuthMiddleware,
        api_key=settings.mcp_api_key.get_secret_value(),
        limit_per_minute=settings.mcp_rate_limit_per_minute,
        clock=clock,
    )
    return app


class _Tools:
    def __init__(
        self,
        settings: Settings,
        linter: Linter | None,
        agent_deps: AgentDeps | None,
        git_host: GitHost | None,
        store: AnalysisStore,
    ) -> None:
        self._settings = settings
        self._linter = linter
        self._agent_deps = agent_deps
        self._git_host = git_host
        self._store = store

    async def lint_spec(
        self,
        spec_content: str | None = None,
        repo: str | None = None,
        path: str | None = None,
        ref: str = "main",
    ) -> dict[str, Any]:
        """Lint an OpenAPI 3.0/3.1 spec against the governance rules. Pass either the spec text
        (spec_content, YAML or JSON) or its location on an allowlisted GitHub repository
        (repo as OWNER/NAME, path, ref)."""
        log.info("tool_called", tool="lint_spec")
        with _anticipated():
            text = await anyio.to_thread.run_sync(self._read_spec, spec_content, repo, path, ref)
            violations = await anyio.to_thread.run_sync(self._lint, text)
        return {"count": len(violations), "violations": [_violation(v) for v in violations]}

    async def propose_fixes(
        self,
        spec_content: str | None = None,
        repo: str | None = None,
        path: str | None = None,
        ref: str = "main",
    ) -> dict[str, Any]:
        """Propose verified fixes for every violation (same inputs as lint_spec). Nothing is
        applied: show the user each proposal's diff and breaking flag, then call apply_fixes or
        open_pull_request with the approved ids. The analysis expires after an hour."""
        log.info("tool_called", tool="propose_fixes")
        with _anticipated():
            text = await anyio.to_thread.run_sync(self._read_spec, spec_content, repo, path, ref)
            report, diffs = await anyio.to_thread.run_sync(self._propose, text)
        stored = self._store.put(text, report)
        proposals = [
            {
                "id": outcome.proposal.id,
                "rule_id": outcome.rule_id,
                "scope_pointer": outcome.scope_pointer,
                "breaking": outcome.proposal.breaking,
                "rationale": outcome.proposal.rationale,
                "diff": diffs[outcome.proposal.id],
            }
            for outcome in report.outcomes
            if outcome.status is FixStatus.RESOLVED and outcome.proposal is not None
        ]
        unresolved = [
            {"rule_id": o.rule_id, "scope_pointer": o.scope_pointer, "status": o.status.value}
            for o in report.outcomes
            if o.status is not FixStatus.RESOLVED
        ]
        return {
            "analysis_id": report.analysis_id,
            "expires_in_seconds": round(stored.expires_at - time.monotonic()),
            "summary": {
                "violations_before": len(report.initial_violations),
                "violations_after_all_fixes": len(report.final_violations),
                "proposals": len(proposals),
                "breaking_proposals": sum(p["breaking"] is True for p in proposals),
                "cost_usd": round(report.usage.cost_usd, 6),
            },
            "proposals": proposals,
            "not_fixed": unresolved,
        }

    async def apply_fixes(self, analysis_id: str, approved_fix_ids: list[str]) -> dict[str, Any]:
        """Apply the fixes the user approved (ids from propose_fixes) to the original spec and
        return the fixed spec. Fails if an approved fix depends on one that was not approved."""
        log.info("tool_called", tool="apply_fixes")
        with _anticipated():
            stored = self._store.get(analysis_id)
            applied = await anyio.to_thread.run_sync(
                lambda: apply_approved(
                    stored.spec_text, stored.report, approved_fix_ids, self._get_linter()
                )
            )
        return {
            "applied": applied.applied,
            "remaining_violations": [_violation(v) for v in applied.remaining],
            "spec_content": applied.spec_text,
        }

    async def open_pull_request(
        self,
        analysis_id: str,
        approved_fix_ids: list[str],
        repo: str,
        path: str,
        base: str = "main",
    ) -> dict[str, Any]:
        """Open a pull request on an allowlisted GitHub repository with the fixes the user
        approved (ids from propose_fixes). Refused if the file on `base` is not the analyzed
        spec, or if an approved fix depends on one that was not approved."""
        log.info("tool_called", tool="open_pull_request")
        with _anticipated():
            stored = self._store.get(analysis_id)
            pull_request = await anyio.to_thread.run_sync(
                lambda: open_fix_pull_request(
                    self._get_git_host(),
                    repo=repo,
                    path=path,
                    base=base,
                    original_text=stored.spec_text,
                    report=stored.report,
                    approved_ids=approved_fix_ids,
                    linter=self._get_linter(),
                    allowlist=self._settings.repo_allowlist,
                )
            )
        return {
            "pr_url": pull_request.url,
            "branch": pull_request.branch,
            "applied": pull_request.applied,
            "remaining_violations": [_violation(v) for v in pull_request.remaining],
        }

    def _read_spec(
        self, spec_content: str | None, repo: str | None, path: str | None, ref: str
    ) -> str:
        if spec_content is not None and repo is None and path is None:
            return spec_content
        if spec_content is None and repo is not None and path is not None:
            check_repo_allowed(repo, self._settings.repo_allowlist)
            return self._get_git_host().get_file(repo, path, ref)[0]
        raise UsageError("pass either spec_content, or repo and path (and optionally ref)")

    def _lint(self, spec_content: str) -> list[Violation]:
        load_spec(spec_content)  # clear error for invalid / multi-file specs
        return self._get_linter().lint(spec_content)

    def _propose(self, spec_content: str) -> tuple[AnalysisReport, dict[str, str]]:
        from govagent.agent.runner import analyze

        report = analyze(spec_content, self._get_agent_deps(), wiring.budget(self._settings))
        return report, proposal_diffs(spec_content, report)

    def _get_linter(self) -> Linter:
        if self._linter is None:
            self._linter = wiring.linter(self._settings)
        return self._linter

    def _get_git_host(self) -> GitHost:
        if self._git_host is None:  # built lazily: specs passed as text need no token
            self._git_host = wiring.git_host(self._settings)
        return self._git_host

    def _get_agent_deps(self) -> AgentDeps:
        if self._agent_deps is None:  # built lazily: linting works without a model configured
            self._agent_deps = wiring.agent_deps(self._settings)
        return self._agent_deps


class UsageError(GovagentError):
    """A tool was called with an invalid combination of arguments."""


@contextmanager
def _anticipated() -> Generator[None]:
    """Our errors are expected failures: their message goes to the model (ToolError).
    Anything else is a crash, which the SDK reports without its message."""
    try:
        yield
    except GovagentError as exc:
        raise ToolError(str(exc)) from exc


def _violation(v: Violation) -> dict[str, str]:
    return {
        "rule_id": v.rule_id,
        "severity": v.severity.value,
        "pointer": v.pointer,
        "message": v.message,
    }


class BearerAuthMiddleware:
    """`Authorization: Bearer <key>` on every route but /healthz, plus a per-key rate limit."""

    def __init__(
        self, app: ASGIApp, api_key: str, limit_per_minute: int, clock: Callable[[], float]
    ) -> None:
        self._app = app
        self._expected = f"Bearer {api_key}".encode()
        self._limit = limit_per_minute
        self._clock = clock
        self._calls: dict[bytes, deque[float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/healthz":
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        presented = headers.get(b"authorization", b"")
        if not hmac.compare_digest(presented, self._expected):  # constant-time comparison
            log.warning("request_rejected", reason="bad_token", path=scope["path"])
            response = JSONResponse(
                {"error": "missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        if not self._allow(presented):
            log.warning("request_rejected", reason="rate_limit", path=scope["path"])
            response = JSONResponse(
                {"error": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"}
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _allow(self, key: bytes) -> bool:
        """Sliding window: at most `limit` requests in the last 60 seconds for this key."""
        now = self._clock()
        calls = self._calls.setdefault(key, deque())
        while calls and calls[0] <= now - 60:
            calls.popleft()
        if len(calls) >= self._limit:
            return False
        calls.append(now)
        return True
