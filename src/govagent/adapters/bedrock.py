"""FixModel port implemented with Amazon Bedrock (Converse API) through langchain-aws.

Structured output uses tool calling: the model must fill the LLMFixOutput JSON schema, and
langchain validates the answer into the Pydantic model. Every call is metered (tokens -> USD
with the configured prices), including calls whose answer does not match the schema.
"""

from typing import Any, cast

from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
    BotoCoreError,
    ClientError,
)
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel

from govagent.config import Settings
from govagent.domain.errors import ConfigError, LLMError, ModelOutputError
from govagent.domain.models import FixRequest, LLMFixOutput, Usage


class BedrockFixModel:
    def __init__(
        self,
        chat_model: BaseChatModel,
        price_input_per_mtok: float,
        price_output_per_mtok: float,
    ) -> None:
        self._structured = chat_model.with_structured_output(  # pyright: ignore[reportUnknownMemberType]
            LLMFixOutput, include_raw=True
        )
        self._price_input = price_input_per_mtok
        self._price_output = price_output_per_mtok

    @classmethod
    def from_settings(cls, settings: Settings, max_tokens: int = 2048) -> "BedrockFixModel":
        """Fails fast without model, region or prices: the cost budget needs the prices."""
        missing = [
            name
            for name, value in (
                ("GOVAGENT_MODEL_ID", settings.model_id),
                ("GOVAGENT_AWS_REGION", settings.aws_region),
                ("GOVAGENT_PRICE_INPUT_PER_MTOK", settings.price_input_per_mtok),
                ("GOVAGENT_PRICE_OUTPUT_PER_MTOK", settings.price_output_per_mtok),
            )
            if value is None or value == ""
        ]
        if missing:
            raise ConfigError(f"missing settings to call Bedrock: {', '.join(missing)}")
        return cls(
            build_chat_model(settings, max_tokens),
            cast(float, settings.price_input_per_mtok),
            cast(float, settings.price_output_per_mtok),
        )

    def propose(self, request: FixRequest) -> tuple[LLMFixOutput, Usage]:
        messages = [("system", request.system_prompt), ("human", request.user_prompt)]
        try:
            response = cast(dict[str, Any], self._structured.invoke(messages))  # pyright: ignore[reportUnknownMemberType]
        except (ClientError, BotoCoreError) as exc:
            raise LLMError(f"Bedrock call failed: {exc}") from exc

        usage = self._usage(response.get("raw"))
        parsed = response.get("parsed")
        if response.get("parsing_error") is not None or not isinstance(parsed, LLMFixOutput):
            error = response.get("parsing_error") or "no structured output returned"
            raise ModelOutputError(f"output does not match the schema: {error}", usage)
        return parsed, usage

    def _usage(self, raw_message: Any) -> Usage:
        metadata = cast(dict[str, int], getattr(raw_message, "usage_metadata", None) or {})
        input_tokens = metadata.get("input_tokens", 0)
        output_tokens = metadata.get("output_tokens", 0)
        cost = (input_tokens * self._price_input + output_tokens * self._price_output) / 1_000_000
        return Usage(
            llm_calls=1, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost
        )


def build_chat_model(settings: Settings, max_tokens: int) -> ChatBedrockConverse:
    """Converse client with only the request parameters the configured model accepts."""
    extra_fields = {"thinking": {"type": "disabled"}} if settings.model_disable_thinking else None
    return ChatBedrockConverse(
        model=cast(str, settings.model_id),
        region_name=settings.aws_region,
        temperature=settings.model_temperature,  # None: not sent, the model's default applies
        max_tokens=max_tokens,
        additional_model_request_fields=extra_fields,
    )
