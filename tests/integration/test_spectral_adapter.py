import pytest

from govagent.adapters.spectral import SpectralLinter
from govagent.domain.errors import InvalidSpecError, LinterError
from govagent.domain.models import Severity
from tests.conftest import RULE_FIXTURES_DIR, RULESETS_DIR

pytestmark = pytest.mark.integration

RULESET = RULESETS_DIR / "governance.spectral.yaml"


def test_maps_results_to_violations_with_escaped_pointers() -> None:
    text = (RULE_FIXTURES_DIR / "gov-path-no-trailing-slash" / "fail.yaml").read_text()

    [violation] = SpectralLinter("spectral", RULESET).lint(text)

    assert violation.rule_id == "gov-path-no-trailing-slash"
    assert violation.severity is Severity.ERROR
    assert violation.pointer == "/paths/~1pets~1"
    assert "/pets/" in violation.message


def test_json_specs_are_linted() -> None:
    text = '{"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {}}'

    rules = {v.rule_id for v in SpectralLinter("spectral", RULESET).lint(text)}

    assert rules == {"gov-info-contact"}  # no operation, so nothing to secure


@pytest.mark.parametrize(
    "text",
    ["openapi: 3.0.3\ninfo: [unclosed\n", "hello: world\n"],
)
def test_unlintable_documents_raise(text: str) -> None:
    with pytest.raises(InvalidSpecError):
        SpectralLinter("spectral", RULESET).lint(text)


def test_missing_binary_raises() -> None:
    with pytest.raises(LinterError, match="not found"):
        SpectralLinter("spectral-does-not-exist", RULESET).lint("openapi: 3.0.3\n")


def test_missing_ruleset_raises() -> None:
    with pytest.raises(LinterError, match="no output"):
        SpectralLinter("spectral", RULESETS_DIR / "nope.yaml").lint("openapi: 3.0.3\n")
