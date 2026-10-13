"""MCP server (docs/SPEC.md §8): streamable HTTP on /mcp, stateless, behind a Bearer token.

Tools: `lint_spec`, `propose_fixes` (runs the agent, stores the analysis) and `apply_fixes`
(applies the fixes the user approved). PR creation (`open_pull_request`) arrives with the
GitHub adapter in M6. The core is synchronous: tools run it in a worker thread so the event
loop stays free.
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
from govagent.core.spec_io import load_spec
from govagent.domain.errors import ConfigError, GovagentError
from govagent.domain.models import AnalysisReport, FixStatus, Violation
from govagent.domain.ports import Linter
from govagent.interfaces import wiring
from govagent.interfaces.analysis_store import AnalysisStore

INSTRUCTIONS = """\
govagent checks OpenAPI 3.0/3.1 specs against API governance rules and proposes verified
JSON Patch fixes. Typical flow: lint_spec -> propose_fixes -> show the user each diff ->
apply_fixes with the ids the user approved. Fixes marked breaking change the API contract:
only apply them when the user explicitly approves them.
"""


def build_app(
    settings: Settings,
    *,
    linter: Linter | None = None,
    agent_deps: AgentDeps | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Starlette:
    """The ASGI app. `linter` / `agent_deps` default to the real adapters (tests inject fakes)."""
    if settings.mcp_api_key is None or not settings.mcp_api_key.get_secret_value():
        raise ConfigError("GOVAGENT_MCP_API_KEY must be set to run the MCP server")
    tools = _Tools(settings, linter, agent_deps, AnalysisStore(settings.analysis_ttl_seconds))
    server = MCPServer(name="govagent", instructions=INSTRUCTIONS)
    server.tool()(tools.lint_spec)
    server.tool()(tools.propose_fixes)
    server.tool()(tools.apply_fixes)

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
        store: AnalysisStore,
    ) -> None:
        self._settings = settings
        self._linter = linter
        self._agent_deps = agent_deps
        self._store = store

    async def lint_spec(self, spec_content: str) -> dict[str, Any]:
        """Lint an OpenAPI 3.0/3.1 spec (YAML or JSON text) against the governance rules."""
        with _anticipated():
            violations = await anyio.to_thread.run_sync(self._lint, spec_content)
        return {"count": len(violations), "violations": [_violation(v) for v in violations]}

    async def propose_fixes(self, spec_content: str) -> dict[str, Any]:
        """Propose verified fixes for every violation. Nothing is applied: show the user each
        proposal's diff and breaking flag, then call apply_fixes with the approved ids. The
        analysis expires after an hour."""
        with _anticipated():
            report, diffs = await anyio.to_thread.run_sync(self._propose, spec_content)
        stored = self._store.put(spec_content, report)
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

    def _get_agent_deps(self) -> AgentDeps:
        if self._agent_deps is None:  # built lazily: linting works without a model configured
            self._agent_deps = wiring.agent_deps(self._settings)
        return self._agent_deps


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
            response = JSONResponse(
                {"error": "missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        if not self._allow(presented):
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
