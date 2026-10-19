"""The README's agent diagram must be the compiled graph, not a hand-drawn copy."""

import re
from pathlib import Path

from govagent.agent.graph import draw_mermaid

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_graph_matches_the_compiled_graph() -> None:
    block = re.search(
        r"<!-- agent-graph:start -->\n```mermaid\n(.*?)\n```\n<!-- agent-graph:end -->",
        README.read_text(),
        re.DOTALL,
    )

    assert block is not None, "agent-graph markers not found in README.md"
    assert block.group(1).strip() == draw_mermaid().strip(), (
        "README graph is stale: paste the output of `uv run python -m govagent.agent.graph`"
    )
