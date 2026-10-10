"""Dataset sanity with the real linter: every case reports exactly its injected violations."""

import pytest

from evals.build_dataset import check_cases, load_cases
from govagent.adapters.spectral import SpectralLinter
from tests.conftest import RULESETS_DIR

pytestmark = pytest.mark.integration


def test_spectral_detects_exactly_the_injected_violations_of_every_case() -> None:
    linter = SpectralLinter("spectral", RULESETS_DIR / "governance.spectral.yaml")

    assert check_cases(load_cases(), linter) == []
