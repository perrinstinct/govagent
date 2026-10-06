"""Walking skeleton end to end with real Spectral and a scripted model (no network)."""

import pytest

from govagent.adapters.spectral import SpectralLinter
from govagent.config import Settings
from govagent.domain.errors import ScopeError
from govagent.domain.models import PatchOp
from govagent.interfaces.skeleton import LLMFixOutput, Proposal, Proposer, run
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

SPEC_TEXT = (REPO_ROOT / "tests" / "fixtures" / "specs" / "pets.yaml").read_text()
TARGET = "/paths/~1pets/post"
LINTER = SpectralLinter("spectral", REPO_ROOT / "rulesets" / "governance.spectral.yaml")


@pytest.fixture
def settings(clean_env: pytest.MonkeyPatch) -> Settings:
    clean_env.chdir(REPO_ROOT)
    clean_env.setenv("GOVAGENT_PRICE_INPUT_PER_MTOK", "1.0")
    clean_env.setenv("GOVAGENT_PRICE_OUTPUT_PER_MTOK", "5.0")
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def scripted(*ops: PatchOp, prompts: list[str] | None = None) -> Proposer:
    def propose(system: str, user: str) -> Proposal:
        if prompts is not None:
            prompts.append(user)
        output = LLMFixOutput(ops=list(ops), rationale="scripted", needs_human_input=False)
        return Proposal(output, input_tokens=1000, output_tokens=100)

    return propose


def test_resolves_violation_and_preserves_comments(settings: Settings) -> None:
    prompts: list[str] = []
    propose = scripted(
        PatchOp(op="add", path=f"{TARGET}/summary", value="Create a pet"), prompts=prompts
    )

    result = run(SPEC_TEXT, settings, LINTER, propose)

    assert result.target == f"gov-operation-summary:{TARGET}"
    assert result.resolved
    assert result.introduced == []
    assert result.patched_text is not None
    assert "summary: Create a pet" in result.patched_text
    assert "# team mailbox" in result.patched_text
    assert "# display name, not unique" in result.patched_text
    assert result.cost_usd == pytest.approx(0.0015)
    assert f"Allowed scope pointer (write only under it): {TARGET}" in prompts[0]


def test_wrong_fix_is_not_resolved(settings: Settings) -> None:
    propose = scripted(PatchOp(op="add", path=f"{TARGET}/x-note", value="hello"))

    result = run(SPEC_TEXT, settings, LINTER, propose)

    assert not result.resolved


def test_out_of_scope_op_is_rejected_before_any_change(settings: Settings) -> None:
    propose = scripted(
        PatchOp(op="add", path=f"{TARGET}/summary", value="Create a pet"),
        PatchOp(op="replace", path="/servers/0/url", value="http://api.example.com"),
    )

    with pytest.raises(ScopeError, match="/servers/0/url"):
        run(SPEC_TEXT, settings, LINTER, propose)


def test_clean_spec_needs_no_call(settings: Settings) -> None:
    calls: list[str] = []
    clean = SPEC_TEXT.replace("    post:\n", "    post:\n      summary: Create a pet\n")

    result = run(clean, settings, LINTER, scripted(prompts=calls))

    assert result.target is None
    assert calls == []
