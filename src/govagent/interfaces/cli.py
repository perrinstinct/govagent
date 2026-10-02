"""Command-line interface. Commands are added milestone by milestone (see docs/SPEC.md §8)."""

import typer

from govagent import __version__

app = typer.Typer(name="govagent", no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """API governance agent for OpenAPI specs."""


@app.command()
def version() -> None:
    """Show the govagent version."""
    typer.echo(__version__)
