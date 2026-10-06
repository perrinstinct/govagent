"""M1 acceptance: a real Bedrock call resolves the violation. Costs money; run only when asked."""

import pytest

from govagent.adapters.spectral import SpectralLinter
from govagent.config import Settings
from govagent.interfaces.skeleton import bedrock_proposer, run
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.bedrock


def test_real_model_resolves_missing_summary() -> None:
    settings = Settings()  # reads .env: model, region, prices
    spec_text = (REPO_ROOT / "tests" / "fixtures" / "specs" / "pets.yaml").read_text()

    linter = SpectralLinter(settings.spectral_bin, settings.ruleset_path)

    result = run(spec_text, settings, linter, bedrock_proposer(settings))

    assert result.resolved, result
