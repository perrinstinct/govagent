"""Command-line interface. Commands are added milestone by milestone (see docs/SPEC.md §8)."""

from pathlib import Path
from typing import Annotated

import typer

from govagent import __version__
from govagent.config import Settings
from govagent.domain.errors import GovagentError

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
    skeleton: Annotated[
        bool, typer.Option("--skeleton", help="M1 walking skeleton: one rule, one LLM call.")
    ] = False,
    out: Annotated[Path | None, typer.Option(help="Write the patched spec to this file.")] = None,
) -> None:
    """Propose and verify fixes for governance violations (calls the configured LLM)."""
    if not skeleton:
        typer.echo("Only `--skeleton` is implemented so far (M1).", err=True)
        raise typer.Exit(2)

    from govagent.interfaces import skeleton as walking_skeleton

    settings = Settings()
    try:
        propose = walking_skeleton.bedrock_proposer(settings)
        result = walking_skeleton.run(spec.read_text(), settings, propose)
    except GovagentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if result.target is None:
        typer.echo(f"No {walking_skeleton.RULE_ID} violation found.")
        return
    if result.proposal is not None:
        output = result.proposal.output
        typer.echo(f"target:    {result.target}")
        typer.echo(f"rationale: {output.rationale}")
        for op in output.ops:
            typer.echo(f"op:        {op.model_dump_json(by_alias=True, exclude_none=True)}")
        cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.6f}"
        typer.echo(
            f"usage:     {result.proposal.input_tokens} in / "
            f"{result.proposal.output_tokens} out tokens, cost {cost}"
        )
    if result.introduced:
        typer.echo(f"introduced: {', '.join(result.introduced)}")
    typer.echo(f"resolved:  {result.resolved}")
    if out is not None and result.patched_text is not None:
        out.write_text(result.patched_text)
        typer.echo(f"written:   {out}")
    if not result.resolved:
        raise typer.Exit(1)
