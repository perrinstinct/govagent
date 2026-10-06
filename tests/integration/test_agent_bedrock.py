"""End to end with real Spectral and real Bedrock. Costs money: run only when asked."""

import pytest

from govagent.adapters.bedrock import BedrockFixModel
from govagent.adapters.spectral import SpectralLinter
from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import analyze
from govagent.agent.state import Budget
from govagent.config import Settings
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.models import FixStatus
from tests.conftest import RULE_FIXTURES_DIR

pytestmark = [pytest.mark.bedrock, pytest.mark.integration]


def test_real_model_resolves_missing_summary() -> None:
    settings = Settings()  # reads .env: model, region, prices
    deps = AgentDeps(
        linter=SpectralLinter(settings.spectral_bin, settings.ruleset_path),
        fix_model=BedrockFixModel.from_settings(settings),
        rules_meta=parse_rules_meta(settings.rules_meta_path.read_text()),
    )
    budget = Budget(max_attempts_per_group=3, max_llm_calls_per_run=3, max_cost_usd_per_run=0.05)
    spec = (RULE_FIXTURES_DIR / "gov-operation-summary" / "fail.yaml").read_text()

    report = analyze(spec, deps, budget)

    assert [o.status for o in report.outcomes] == [FixStatus.RESOLVED]
    assert report.final_violations == []
    assert 0 < report.usage.cost_usd < 0.05
