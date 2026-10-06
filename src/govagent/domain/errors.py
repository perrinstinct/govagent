"""Project exceptions. Callers catch `GovagentError`; nothing is swallowed silently."""

from govagent.domain.models import Usage


class GovagentError(Exception):
    """Base class for every error raised by govagent."""


class ConfigError(GovagentError):
    """A required setting is missing or inconsistent."""


class LinterError(GovagentError):
    """The linter crashed or returned unreadable output (violations are not errors)."""


class PatchError(GovagentError):
    """A patch operation cannot be applied (invalid pointer, missing target...)."""


class ScopeError(GovagentError):
    """A patch operation writes outside the allowed scopes."""


class LLMError(GovagentError):
    """The model provider refused or failed the call (access, throttling, network...)."""


class PointerError(GovagentError):
    """A JSON pointer is malformed or does not resolve in the document."""


class InvalidSpecError(GovagentError):
    """The input is not a parseable, valid OpenAPI 3.0/3.1 document."""


class UnsupportedSpecError(GovagentError):
    """The spec uses a feature outside the MVP (e.g. external `$ref`, multi-file specs)."""


class GroupingError(GovagentError):
    """A violation cannot be mapped to a fix scope (ruleset / rules_meta mismatch)."""


class ModelOutputError(GovagentError):
    """The model answered, but not in the expected schema. The call was still paid for."""

    def __init__(self, message: str, usage: Usage) -> None:
        super().__init__(message)
        self.usage = usage
