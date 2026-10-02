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
