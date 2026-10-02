from pathlib import Path

import pytest
from pydantic import ValidationError

from govagent.config import Settings


def make_settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.usefixtures("clean_env")
def test_defaults_match_spec_budgets() -> None:
    settings = make_settings()

    assert settings.max_attempts_per_group == 3
    assert settings.max_llm_calls_per_run == 40
    assert settings.max_cost_usd_per_run == 0.50
    assert settings.analysis_ttl_seconds == 3600
    assert settings.ruleset_path == Path("rulesets/governance.spectral.yaml")


@pytest.mark.usefixtures("clean_env")
def test_no_hardcoded_model_region_prices_or_secrets() -> None:
    settings = make_settings()

    assert settings.model_id is None
    assert settings.aws_region is None
    assert settings.price_input_per_mtok is None
    assert settings.price_output_per_mtok is None
    assert settings.github_token is None
    assert settings.mcp_api_key is None
    assert settings.langfuse_enabled is False


def test_reads_prefixed_env_vars(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GOVAGENT_MODEL_ID", "some-model")
    clean_env.setenv("GOVAGENT_MAX_ATTEMPTS_PER_GROUP", "5")
    clean_env.setenv("GOVAGENT_PRICE_INPUT_PER_MTOK", "3.0")
    clean_env.setenv("GOVAGENT_LOG_LEVEL", "debug")

    settings = make_settings()

    assert settings.model_id == "some-model"
    assert settings.max_attempts_per_group == 5
    assert settings.price_input_per_mtok == 3.0
    assert settings.log_level == "DEBUG"


def test_secrets_are_masked(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GOVAGENT_GITHUB_TOKEN", "ghp_secret")

    settings = make_settings()

    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_secret"
    assert "ghp_secret" not in repr(settings)


def test_repo_allowlist_is_comma_separated(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GOVAGENT_REPO_ALLOWLIST", "octo/demo, octo/other ,")

    assert make_settings().repo_allowlist == ["octo/demo", "octo/other"]


def test_repo_allowlist_rejects_malformed_entries(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GOVAGENT_REPO_ALLOWLIST", "octo/demo,not-a-repo")

    with pytest.raises(ValidationError, match="not-a-repo"):
        make_settings()


def test_empty_values_fall_back_to_defaults(clean_env: pytest.MonkeyPatch) -> None:
    # .env.example ships with empty values; they must not fail parsing.
    clean_env.setenv("GOVAGENT_PRICE_INPUT_PER_MTOK", "")
    clean_env.setenv("GOVAGENT_MAX_ATTEMPTS_PER_GROUP", "")

    settings = make_settings()

    assert settings.price_input_per_mtok is None
    assert settings.max_attempts_per_group == 3


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GOVAGENT_MAX_ATTEMPTS_PER_GROUP", "0"),
        ("GOVAGENT_MAX_COST_USD_PER_RUN", "0"),
        ("GOVAGENT_PRICE_OUTPUT_PER_MTOK", "-1"),
    ],
)
def test_rejects_invalid_budgets_and_prices(
    clean_env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    clean_env.setenv(name, value)

    with pytest.raises(ValidationError):
        make_settings()


def test_env_example_lists_every_setting() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    declared = {
        line.split("=", 1)[0]
        for line in env_example.read_text().splitlines()
        if line and not line.startswith("#")
    }

    expected = {f"GOVAGENT_{name.upper()}" for name in Settings.model_fields}
    assert declared == expected
