"""Entry point of the agent: spec text in, AnalysisReport out (no approval, no PR)."""

import hashlib
import time
import uuid
from collections import Counter

from govagent.agent.graph import build_graph
from govagent.agent.nodes import AgentDeps
from govagent.agent.state import AgentState, Budget
from govagent.core.spec_io import load_spec
from govagent.domain.models import AnalysisReport
from govagent.observability import get_logger, run_context

log = get_logger(__name__)

# LangGraph aborts after this many node executions. The run is bounded by the budgets anyway:
# every loop either spends an LLM call (capped) or settles a group (finite), so this is only a
# safety net against wiring bugs.
_RECURSION_LIMIT = 10_000


def run_agent(
    spec_text: str, deps: AgentDeps, budget: Budget, run_id: str | None = None
) -> AgentState:
    """Run the fix loop and return its final state (working spec included).

    Every log line of the run carries `run_id` (a new one if not given)."""
    with run_context(run_id or uuid.uuid4().hex):
        load_spec(spec_text)  # fail fast on unsupported / invalid input, before any model call
        log.info("analysis_started", spec_bytes=len(spec_text.encode()))
        started = time.monotonic()
        final = AgentState.model_validate(
            build_graph(deps).invoke(
                AgentState(budget=budget, spec_text=spec_text),
                config={"recursion_limit": _RECURSION_LIMIT},
            )
        )
        log.info(
            "analysis_finished",
            violations_before=len(final.initial_violations),
            violations_after=len(final.violations),
            outcomes=dict(Counter(o.status.value for o in final.outcomes)),
            llm_calls=final.usage.llm_calls,
            cost_usd=round(final.usage.cost_usd, 6),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return final


def analyze(spec_text: str, deps: AgentDeps, budget: Budget) -> AnalysisReport:
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    state = run_agent(spec_text, deps, budget, run_id)
    return AnalysisReport(
        analysis_id=run_id,  # the same id in the logs and in the report
        spec_sha256=hashlib.sha256(spec_text.encode()).hexdigest(),
        initial_violations=state.initial_violations,
        outcomes=state.outcomes,
        final_violations=state.violations,  # lint of the spec with every RESOLVED fix applied
        usage=state.usage,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
