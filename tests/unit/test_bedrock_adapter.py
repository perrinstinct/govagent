"""BedrockFixModel with a stand-in chat model: parsing, metering and error mapping."""

from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError  # pyright: ignore[reportMissingTypeStubs]

from govagent.adapters.bedrock import BedrockFixModel
from govagent.config import Settings
from govagent.domain.errors import ConfigError, LLMError, ModelOutputError
from govagent.domain.models import FixRequest, LLMFixOutput

REQUEST = FixRequest(group_id="g", attempt=1, system_prompt="sys", user_prompt="user")
OUTPUT = LLMFixOutput(ops=[], rationale="r", needs_human_input=True)


class StubChat:
    """Plays the part of ChatBedrockConverse.with_structured_output(..., include_raw=True)."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.messages: Any = None

    def with_structured_output(self, schema: Any, include_raw: bool) -> "StubChat":
        assert schema is LLMFixOutput
        assert include_raw
        return self

    def invoke(self, messages: Any) -> Any:
        self.messages = messages
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def raw(input_tokens: int = 2000, output_tokens: int = 300) -> Any:
    return SimpleNamespace(
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens}
    )


def model(response: Any) -> tuple[BedrockFixModel, StubChat]:
    chat = StubChat(response)
    return BedrockFixModel(chat, 1.10, 5.50), chat  # type: ignore[arg-type]


def test_returns_parsed_output_and_metered_usage() -> None:
    fix_model, chat = model({"raw": raw(), "parsed": OUTPUT, "parsing_error": None})

    output, usage = fix_model.propose(REQUEST)

    assert output == OUTPUT
    assert chat.messages == [("system", "sys"), ("human", "user")]
    assert usage.llm_calls == 1
    assert (usage.input_tokens, usage.output_tokens) == (2000, 300)
    assert usage.cost_usd == pytest.approx((2000 * 1.10 + 300 * 5.50) / 1_000_000)


def test_schema_mismatch_raises_with_the_usage_of_the_paid_call() -> None:
    fix_model, _ = model({"raw": raw(), "parsed": None, "parsing_error": ValueError("no ops")})

    with pytest.raises(ModelOutputError, match="no ops") as caught:
        fix_model.propose(REQUEST)
    assert caught.value.usage.llm_calls == 1
    assert caught.value.usage.cost_usd > 0


def test_provider_errors_become_llm_errors() -> None:
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")
    fix_model, _ = model(denied)

    with pytest.raises(LLMError, match="AccessDenied"):
        fix_model.propose(REQUEST)


def test_from_settings_requires_model_region_and_prices(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GOVAGENT_MODEL_ID", "some-model")

    with pytest.raises(ConfigError) as caught:
        BedrockFixModel.from_settings(Settings(_env_file=None))  # pyright: ignore[reportCallIssue]
    message = str(caught.value)
    assert "GOVAGENT_MODEL_ID" not in message
    assert "GOVAGENT_AWS_REGION" in message
    assert "GOVAGENT_PRICE_INPUT_PER_MTOK" in message
