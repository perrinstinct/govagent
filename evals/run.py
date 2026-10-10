"""Run the agent on the eval dataset: `uv run python -m evals.run --subset smoke`.

Calls the real model (Bedrock) for every case: this costs money. `--max-total-cost` stops the
run before the next case once the cap is reached (per-case budgets from Settings still apply).
Writes evals/results/<date>_<subset>_<model>.json (summary + every case) and .md (tables).
"""

import datetime as dt
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from evals.build_dataset import DATASET_PATH, Case, InjectedViolation, load_cases, smoke_subset
from evals.metrics import CaseResult, OutcomeRecord, Summary, render_markdown, summarize
from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import run_agent
from govagent.agent.state import Budget
from govagent.core.spec_io import load_spec
from govagent.domain.errors import GovagentError, InvalidSpecError, UnsupportedSpecError
from govagent.domain.models import Usage, Violation

RESULTS_DIR = Path(__file__).resolve().parent / "results"


class EvalRun(BaseModel, frozen=True):
    summary: Summary
    cases: list[CaseResult]


def evaluate(
    cases: Sequence[Case],
    deps: AgentDeps,
    budget: Budget,
    max_total_cost_usd: float,
    on_case: Callable[[int, CaseResult], None] | None = None,
) -> tuple[list[CaseResult], int]:
    """Run every case in order; stop before a case once the global cost cap is reached."""
    results: list[CaseResult] = []
    spent = 0.0
    for index, case in enumerate(cases):
        if spent >= max_total_cost_usd:
            return results, len(cases) - index
        result = _run_case(case, deps, budget)
        spent += result.usage.cost_usd
        results.append(result)
        if on_case is not None:
            on_case(index, result)
    return results, 0


def _run_case(case: Case, deps: AgentDeps, budget: Budget) -> CaseResult:
    started = time.monotonic()
    common: dict[str, Any] = {
        "case_id": case.id,
        "seed": case.seed,
        "kind": case.kind,
        "injected": case.injected,
    }
    try:
        state = run_agent(case.spec_text, deps, budget)
    except GovagentError as exc:
        return CaseResult(
            **common,
            initial=[],
            final=list(case.injected),
            outcomes=[],
            final_valid=False,
            usage=Usage(),
            latency_ms=_elapsed_ms(started),
            error=f"{type(exc).__name__}: {exc}",
        )
    try:
        load_spec(state.spec_text)
        final_valid = True
    except (InvalidSpecError, UnsupportedSpecError):
        final_valid = False
    return CaseResult(
        **common,
        initial=_violations(state.initial_violations),
        final=_violations(state.violations),
        outcomes=[
            OutcomeRecord(rule_id=o.rule_id, status=o.status, attempts=o.attempts)
            for o in state.outcomes
        ],
        final_valid=final_valid,
        usage=state.usage,
        latency_ms=_elapsed_ms(started),
    )


def _violations(violations: Sequence[Violation]) -> list[InjectedViolation]:
    return [InjectedViolation(rule_id=v.rule_id, pointer=v.pointer) for v in violations]


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def model_slug(model_id: str) -> str:
    name = model_id.split("anthropic.")[-1]
    return re.sub(r"[^a-z0-9.-]+", "-", name.lower()).strip("-")


def main(
    subset: Annotated[
        str, typer.Option(help="smoke (12 cases) or full (whole dataset).")
    ] = "smoke",
    model: Annotated[
        str | None, typer.Option(help="Model id (default: GOVAGENT_MODEL_ID).")
    ] = None,
    price_input: Annotated[
        float | None, typer.Option(help="USD per million input tokens for --model.")
    ] = None,
    price_output: Annotated[
        float | None, typer.Option(help="USD per million output tokens for --model.")
    ] = None,
    max_total_cost: Annotated[
        float, typer.Option(help="Stop the run once this many USD have been spent.")
    ] = 1.0,
    dataset: Annotated[Path, typer.Option(help="JSONL dataset.")] = DATASET_PATH,
) -> None:
    from govagent.adapters.bedrock import BedrockFixModel
    from govagent.adapters.spectral import SpectralLinter
    from govagent.config import Settings
    from govagent.core.rules_meta import parse_rules_meta

    if subset not in ("smoke", "full"):
        raise typer.BadParameter("subset must be 'smoke' or 'full'")
    if model is not None and (price_input is None or price_output is None):
        raise typer.BadParameter("--model needs --price-input and --price-output")
    overrides = {
        "model_id": model,
        "price_input_per_mtok": price_input,
        "price_output_per_mtok": price_output,
    }
    settings = Settings().model_copy(update={k: v for k, v in overrides.items() if v is not None})
    deps = AgentDeps(
        linter=SpectralLinter(settings.spectral_bin, settings.ruleset_path),
        fix_model=BedrockFixModel.from_settings(settings),
        rules_meta=parse_rules_meta(settings.rules_meta_path.read_text()),
    )
    budget = Budget(
        max_attempts_per_group=settings.max_attempts_per_group,
        max_llm_calls_per_run=settings.max_llm_calls_per_run,
        max_cost_usd_per_run=settings.max_cost_usd_per_run,
    )
    all_cases = load_cases(dataset)
    cases = smoke_subset(all_cases) if subset == "smoke" else all_cases
    model_id = str(settings.model_id)
    typer.echo(f"{len(cases)} cases, model {model_id}, cost cap ${max_total_cost:.2f}")

    def progress(index: int, result: CaseResult) -> None:
        statuses = ",".join(o.status.value for o in result.outcomes) or result.error or "-"
        typer.echo(
            f"[{index + 1}/{len(cases)}] {result.case_id}: {statuses} "
            f"${result.usage.cost_usd:.4f} {result.latency_ms / 1000:.1f}s"
        )

    results, skipped = evaluate(cases, deps, budget, max_total_cost, progress)
    summary = summarize(model_id, subset, results, skipped)
    RESULTS_DIR.mkdir(exist_ok=True)
    stem = f"{dt.date.today().isoformat()}_{subset}_{model_slug(model_id)}"
    run = EvalRun(summary=summary, cases=results)
    (RESULTS_DIR / f"{stem}.json").write_text(run.model_dump_json(indent=2) + "\n")
    (RESULTS_DIR / f"{stem}.md").write_text(render_markdown(summary))
    typer.echo(render_markdown(summary))
    typer.echo(f"results written to {RESULTS_DIR / stem}.{{json,md}}")


if __name__ == "__main__":
    typer.run(main)
