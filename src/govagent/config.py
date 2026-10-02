"""Application settings, loaded from `GOVAGENT_*` environment variables (and `.env`)."""

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class Settings(BaseSettings):
    """Single source of configuration. Model IDs, region, prices and secrets have no defaults."""

    model_config = SettingsConfigDict(
        env_prefix="GOVAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # LLM (Bedrock)
    model_id: str | None = None
    aws_region: str | None = None
    price_input_per_mtok: float | None = Field(default=None, ge=0)
    price_output_per_mtok: float | None = Field(default=None, ge=0)

    # Ruleset and linter
    ruleset_path: Path = Path("rulesets/governance.spectral.yaml")
    rules_meta_path: Path = Path("rulesets/rules_meta.yaml")
    spectral_bin: str = "spectral"

    # Budgets
    max_attempts_per_group: int = Field(default=3, ge=1)
    max_llm_calls_per_run: int = Field(default=40, ge=1)
    max_cost_usd_per_run: float = Field(default=0.50, gt=0)

    # GitHub
    github_token: SecretStr | None = None
    repo_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list[str])

    # MCP server
    mcp_api_key: SecretStr | None = None
    analysis_ttl_seconds: int = Field(default=3600, ge=1)

    # Observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None

    @field_validator("repo_allowlist", mode="before")
    @classmethod
    def _split_allowlist(cls, value: object) -> object:
        """Accept a comma-separated string: `owner/a,owner/b`."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("repo_allowlist")
    @classmethod
    def _check_repo_names(cls, value: list[str]) -> list[str]:
        invalid = [repo for repo in value if not _REPO_RE.fullmatch(repo)]
        if invalid:
            raise ValueError(f"expected OWNER/NAME entries, got: {', '.join(invalid)}")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def langfuse_enabled(self) -> bool:
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None
