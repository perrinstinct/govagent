"""Composition root shared by the CLI and the MCP server: Settings -> concrete dependencies."""

from govagent.adapters.bedrock import BedrockFixModel
from govagent.adapters.spectral import SpectralLinter
from govagent.agent.nodes import AgentDeps
from govagent.agent.state import Budget
from govagent.config import Settings
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.ports import Linter


def linter(settings: Settings) -> Linter:
    return SpectralLinter(settings.spectral_bin, settings.ruleset_path)


def agent_deps(settings: Settings) -> AgentDeps:
    """Raises ConfigError when the model is not configured (model id, region, prices)."""
    return AgentDeps(
        linter=linter(settings),
        fix_model=BedrockFixModel.from_settings(settings),
        rules_meta=parse_rules_meta(settings.rules_meta_path.read_text()),
    )


def budget(settings: Settings) -> Budget:
    return Budget(
        max_attempts_per_group=settings.max_attempts_per_group,
        max_llm_calls_per_run=settings.max_llm_calls_per_run,
        max_cost_usd_per_run=settings.max_cost_usd_per_run,
    )
