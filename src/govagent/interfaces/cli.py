"""Command-line interface: composition root (wiring only). Full CLI in M5 (docs/SPEC.md §8)."""

from pathlib import Path
from typing import Annotated

import typer

from govagent import __version__
from govagent.config import Settings
from govagent.domain.errors import GovagentError
from govagent.domain.models import AnalysisReport

app = typer.Typer(name="govagent", no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """API governance agent for OpenAPI specs."""


@app.command()
def version() -> None:
    """Show the govagent version."""
    typer.echo(__version__)


@app.command()
def fix(
    spec: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    report: Annotated[
        Path | None, typer.Option(help="Write the analysis report (JSON) to this file.")
    ] = None,
) -> None:
    """Propose and verify fixes for every violation (calls the configured LLM).

    Nothing is applied to SPEC: approval and output come in M5/M6.
    """
    from govagent.adapters.bedrock import BedrockFixModel
    from govagent.adapters.spectral import SpectralLinter
    from govagent.agent.nodes import AgentDeps
    from govagent.agent.runner import analyze
    from govagent.agent.state import Budget
    from govagent.core.rules_meta import parse_rules_meta

    settings = Settings()
    try:
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
        result = analyze(spec.read_text(), deps, budget)
    except (GovagentError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    _print_report(result)
    if report is not None:
        report.write_text(result.model_dump_json(indent=2, by_alias=True) + "\n")
        typer.echo(f"report written to {report}")


def _print_report(report: AnalysisReport) -> None:
    for outcome in report.outcomes:
        proposal = outcome.proposal
        breaking = " BREAKING" if proposal is not None and proposal.breaking else ""
        scope = outcome.scope_pointer or "/"
        typer.echo(f"{outcome.status.value:18} {outcome.rule_id} at {scope}{breaking}")
        typer.echo(f"{'':18} attempts={outcome.attempts}")
        if proposal is not None:
            typer.echo(f"{'':18} {proposal.rationale}")
    usage = report.usage
    typer.echo(
        f"violations: {len(report.initial_violations)} -> {len(report.final_violations)} | "
        f"llm calls: {usage.llm_calls} | tokens: {usage.input_tokens} in / "
        f"{usage.output_tokens} out | cost: ${usage.cost_usd:.4f} | {report.duration_ms} ms"
    )
