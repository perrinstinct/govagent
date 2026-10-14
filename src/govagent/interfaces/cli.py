"""Command-line interface (docs/SPEC.md §8): composition and presentation only."""

import json
from pathlib import Path
from typing import Annotated

import typer

from govagent import __version__
from govagent.config import Settings
from govagent.domain.errors import GovagentError
from govagent.domain.models import AnalysisReport, FixOutcome, FixProposal, Severity, Violation
from govagent.interfaces import wiring

app = typer.Typer(name="govagent", no_args_is_help=True, add_completion=False)

SpecArgument = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]


@app.callback()
def main() -> None:
    """API governance agent for OpenAPI specs."""


@app.command()
def version() -> None:
    """Show the govagent version."""
    typer.echo(__version__)


@app.command()
def lint(
    spec: SpecArgument,
    output_format: Annotated[
        str, typer.Option("--format", help="table or json.", show_default=True)
    ] = "table",
) -> None:
    """Report governance violations. Exits with 1 when there is an error or warning."""
    if output_format not in ("table", "json"):
        raise typer.BadParameter("--format must be 'table' or 'json'")
    from govagent.core.spec_io import load_spec

    text = spec.read_text()
    with _errors_as_exit():
        load_spec(text)
        violations = wiring.linter(Settings()).lint(text)
    if output_format == "json":
        typer.echo(json.dumps([v.model_dump(mode="json") for v in violations], indent=2))
    else:
        _print_violations(violations)
    if any(v.severity in (Severity.ERROR, Severity.WARN) for v in violations):
        raise typer.Exit(1)


@app.command()
def fix(
    spec: SpecArgument,
    out: Annotated[
        Path | None, typer.Option(help="Write the fixed spec here instead of in place.")
    ] = None,
    report: Annotated[
        Path | None, typer.Option(help="Write the analysis report (JSON) to this file.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option(help="Show what would be applied; write nothing.")
    ] = False,
    interactive: Annotated[
        bool, typer.Option("--interactive", help="Review each fix (diff) and approve it.")
    ] = False,
    yes_non_breaking: Annotated[
        bool, typer.Option("--yes-non-breaking", help="Approve every non-breaking fix.")
    ] = False,
) -> None:
    """Propose verified fixes (calls the configured LLM), then apply the approved ones.

    Without --interactive or --yes-non-breaking, fixes are only shown.
    """
    original = spec.read_text()
    settings = Settings()
    result = _analyze(original, settings, interactive, yes_non_breaking, report)
    approved = _approve(original, result, settings, interactive, yes_non_breaking)
    if approved is None:
        return
    from govagent.core.approval import apply_approved

    with _errors_as_exit():
        applied = apply_approved(original, result, approved, wiring.linter(settings))
    target = out or spec
    if dry_run:
        typer.echo(f"dry run: would apply {len(applied.applied)} fix(es) to {target}")
        return
    target.write_text(applied.spec_text)
    remaining = len(applied.remaining)
    typer.echo(f"applied {len(applied.applied)} fix(es) to {target}; {remaining} violation(s) left")


@app.command()
def pr(
    repo: Annotated[str, typer.Option(help="GitHub repository, OWNER/NAME (allowlisted).")],
    path: Annotated[str, typer.Option(help="Path of the spec in the repository.")],
    base: Annotated[str, typer.Option(help="Branch to read the spec from and target.")] = "main",
    report: Annotated[
        Path | None, typer.Option(help="Write the analysis report (JSON) to this file.")
    ] = None,
    interactive: Annotated[
        bool, typer.Option("--interactive", help="Review each fix (diff) and approve it.")
    ] = False,
    yes_non_breaking: Annotated[
        bool, typer.Option("--yes-non-breaking", help="Approve every non-breaking fix.")
    ] = False,
) -> None:
    """Analyze a spec on GitHub and open a pull request with the approved fixes."""
    from govagent.core.pull_request import check_repo_allowed, open_fix_pull_request

    settings = Settings()
    with _errors_as_exit():
        check_repo_allowed(repo, settings.repo_allowlist)
        host = wiring.git_host(settings)
        original, _ = host.get_file(repo, path, base)
    result = _analyze(original, settings, interactive, yes_non_breaking, report)
    approved = _approve(original, result, settings, interactive, yes_non_breaking)
    if approved is None:
        return
    with _errors_as_exit():
        pull_request = open_fix_pull_request(
            host,
            repo=repo,
            path=path,
            base=base,
            original_text=original,
            report=result,
            approved_ids=approved,
            linter=wiring.linter(settings),
            allowlist=settings.repo_allowlist,
        )
    typer.echo(f"pull request opened: {pull_request.url} ({len(pull_request.applied)} fix(es))")


def _analyze(
    original: str,
    settings: Settings,
    interactive: bool,
    yes_non_breaking: bool,
    report: Path | None,
) -> AnalysisReport:
    if interactive and yes_non_breaking:
        raise typer.BadParameter("use either --interactive or --yes-non-breaking")
    from govagent.agent.runner import analyze

    with _errors_as_exit():
        result = analyze(original, wiring.agent_deps(settings), wiring.budget(settings))
    _print_report(result)
    if report is not None:
        report.write_text(result.model_dump_json(indent=2, by_alias=True) + "\n")
        typer.echo(f"report written to {report}")
    return result


def _approve(
    original: str,
    result: AnalysisReport,
    settings: Settings,
    interactive: bool,
    yes_non_breaking: bool,
) -> list[str] | None:
    """The approved proposal ids, or None when there is nothing to apply."""
    from govagent.core.approval import (
        apply_approvable,
        auto_approvable_ids,
        proposal_diffs,
        resolved_proposals,
    )

    proposals = resolved_proposals(result)
    if not proposals:
        typer.echo("no verified fix to apply")
        return None
    diffs = proposal_diffs(original, result)
    outcomes = {o.proposal.id: o for o in result.outcomes if o.proposal is not None}
    if interactive:
        approved: list[str] = []
        for proposal in proposals:
            _print_proposal(proposal, outcomes[proposal.id], diffs[proposal.id])
            if typer.confirm("Apply this fix?", default=False):
                approved.append(proposal.id)
    elif yes_non_breaking:
        for proposal in proposals:
            if proposal.breaking:
                typer.echo(f"skipped (breaking, needs explicit approval): {proposal.id}")
        with _errors_as_exit():  # automatic approval: best effort, explained
            _, dropped = apply_approvable(
                original, result, auto_approvable_ids(result), wiring.linter(settings)
            )
        for proposal_id in dropped:
            typer.echo(f"skipped (depends on a fix that was not approved): {proposal_id}")
        approved = [i for i in auto_approvable_ids(result) if i not in dropped]
    else:
        for proposal in proposals:
            _print_proposal(proposal, outcomes[proposal.id], diffs[proposal.id])
        typer.echo("nothing applied: use --interactive or --yes-non-breaking to approve fixes")
        return None
    if not approved:
        typer.echo("no fix approved")
        return None
    return approved


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
) -> None:
    """Run the MCP server (streamable HTTP on /mcp, health check on /healthz)."""
    import uvicorn

    from govagent.interfaces.mcp_server import build_app

    with _errors_as_exit():
        server_app = build_app(Settings())
    uvicorn.run(server_app, host=host, port=port)


class _errors_as_exit:  # noqa: N801  (used as a lowercase context manager)
    """Turn project errors into a one-line message and exit code 1."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        # Returning None (never True) tells readers and pyright that nothing is swallowed.
        if isinstance(exc, GovagentError | OSError):
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc


def _print_violations(violations: list[Violation]) -> None:
    if not violations:
        typer.echo("no violation")
        return
    for v in violations:
        color = typer.colors.RED if v.severity is Severity.ERROR else typer.colors.YELLOW
        typer.secho(f"{v.severity.value:5}", fg=color, nl=False)
        typer.echo(f"  {v.rule_id:28} {v.pointer or '/'}  {v.message}")
    typer.echo(f"{len(violations)} violation(s)")


def _print_report(report: AnalysisReport) -> None:
    for outcome in report.outcomes:
        breaking = " BREAKING" if outcome.proposal is not None and outcome.proposal.breaking else ""
        scope = outcome.scope_pointer or "/"
        typer.echo(f"{outcome.status.value:18} {outcome.rule_id} at {scope}{breaking}")
    usage = report.usage
    typer.echo(
        f"violations: {len(report.initial_violations)} -> {len(report.final_violations)} | "
        f"llm calls: {usage.llm_calls} | tokens: {usage.input_tokens} in / "
        f"{usage.output_tokens} out | cost: ${usage.cost_usd:.4f} | {report.duration_ms} ms"
    )


def _print_proposal(proposal: FixProposal, outcome: FixOutcome, diff: str) -> None:
    typer.echo()
    typer.secho(f"[{proposal.id}] {outcome.rule_id} at {outcome.scope_pointer or '/'}", bold=True)
    if proposal.breaking:
        typer.secho(" BREAKING ", fg=typer.colors.WHITE, bg=typer.colors.RED, bold=True)
    typer.echo(proposal.rationale)
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            typer.secho(line, bold=True)
        elif line.startswith("+"):
            typer.secho(line, fg=typer.colors.GREEN)
        elif line.startswith("-"):
            typer.secho(line, fg=typer.colors.RED)
        elif line.startswith("@@"):
            typer.secho(line, fg=typer.colors.CYAN)
        else:
            typer.echo(line)
