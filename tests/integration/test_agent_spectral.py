"""The agent with the real Spectral linter and a scripted model (no network)."""

import pytest

from govagent.adapters.spectral import SpectralLinter
from govagent.agent.nodes import AgentDeps
from govagent.agent.runner import run_agent
from govagent.agent.state import Budget
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.models import FixStatus
from tests.conftest import RULE_FIXTURES_DIR, RULESETS_DIR
from tests.unit.agent_fakes import ScriptedModel, fix

pytestmark = pytest.mark.integration

LINTER = SpectralLinter("spectral", RULESETS_DIR / "governance.spectral.yaml")
RULES_META = parse_rules_meta((RULESETS_DIR / "rules_meta.yaml").read_text())
BUDGET = Budget(max_attempts_per_group=3, max_llm_calls_per_run=10, max_cost_usd_per_run=1.0)

SPEC = (RULE_FIXTURES_DIR / "gov-property-camel" / "fail.yaml").read_text()
PET = "/components/schemas/Pet"
RENAME = {"op": "move", "from": f"{PET}/properties/pet_name", "path": f"{PET}/properties/petName"}
UPDATE_REQUIRED = {"op": "replace", "path": f"{PET}/required/0", "value": "petName"}


def test_property_rename_is_verified_by_spectral_and_flagged_breaking() -> None:
    model = ScriptedModel(fix(RENAME, UPDATE_REQUIRED))

    state = run_agent(SPEC, AgentDeps(LINTER, model, RULES_META), BUDGET)

    [outcome] = state.outcomes
    assert outcome.status is FixStatus.RESOLVED
    assert outcome.proposal is not None
    assert outcome.proposal.breaking is True
    assert state.violations == []
    # The fixed spec is exactly the fail fixture with the two names changed: minimal diff.
    expected = SPEC.replace("required: [pet_name]", "required: [petName]").replace(
        "        pet_name:\n", "        petName:\n"
    )
    assert state.spec_text == expected


def test_rename_that_forgets_required_is_rejected_with_feedback() -> None:
    # Attempt 1 is valid OpenAPI and Spectral is happy, but `required` now dangles.
    model = ScriptedModel(fix(RENAME), fix(RENAME, UPDATE_REQUIRED))

    state = run_agent(SPEC, AgentDeps(LINTER, model, RULES_META), BUDGET)

    assert state.outcomes[0].status is FixStatus.RESOLVED
    assert state.outcomes[0].attempts == 2
    feedback = model.requests[1].user_prompt
    assert "required lists 'pet_name', which is not in its properties" in feedback
