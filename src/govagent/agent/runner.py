"""Entry point of the agent: spec text in, AnalysisReport out (no approval, no PR)."""

import hashlib
import time
import uuid

from govagent.agent.graph import build_graph
from govagent.agent.nodes import AgentDeps
from govagent.agent.state import AgentState, Budget
from govagent.core.spec_io import load_spec
from govagent.domain.models import AnalysisReport

# LangGraph aborts after this many node executions. The run is bounded by the budgets anyway:
# every loop either spends an LLM call (capped) or settles a group (finite), so this is only a
# safety net against wiring bugs.
_RECURSION_LIMIT = 10_000


def run_agent(spec_text: str, deps: AgentDeps, budget: Budget) -> AgentState:
    """Run the fix loop and return its final state (working spec included)."""
    load_spec(spec_text)  # fail fast on unsupported / invalid input, before any model call
    graph = build_graph(deps)
    final = graph.invoke(
        AgentState(budget=budget, spec_text=spec_text),
        config={"recursion_limit": _RECURSION_LIMIT},
    )
    return AgentState.model_validate(final)


def analyze(spec_text: str, deps: AgentDeps, budget: Budget) -> AnalysisReport:
    started = time.monotonic()
    state = run_agent(spec_text, deps, budget)
    return AnalysisReport(
        analysis_id=uuid.uuid4().hex,
        spec_sha256=hashlib.sha256(spec_text.encode()).hexdigest(),
        initial_violations=state.initial_violations,
        outcomes=state.outcomes,
        final_violations=state.violations,  # lint of the spec with every RESOLVED fix applied
        usage=state.usage,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
