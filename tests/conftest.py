from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RULESETS_DIR = REPO_ROOT / "rulesets"
RULE_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "rules"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove any GOVAGENT_* variable inherited from the shell."""
    import os

    for name in list(os.environ):
        if name.startswith("GOVAGENT_"):
            monkeypatch.delenv(name)
    return monkeypatch


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Default test selection.

    - `bedrock` tests make real, paid model calls: they run only when `-m` names them.
    - `integration` tests need Spectral: a bare `pytest` skips them, but they run when selected
      with `-m integration` or targeted explicitly by path / node id (e.g. an IDE run button).
    """
    mark_expression: str = config.option.markexpr
    explicit_targets = config.args_source is pytest.Config.ArgsSource.ARGS
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if _excluded(item, mark_expression, explicit_targets):
            deselected.append(item)
        else:
            selected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


def _excluded(item: pytest.Item, mark_expression: str, explicit_targets: bool) -> bool:
    if item.get_closest_marker("bedrock"):
        return "bedrock" not in mark_expression
    if item.get_closest_marker("integration"):
        return not (mark_expression or explicit_targets)
    return False
