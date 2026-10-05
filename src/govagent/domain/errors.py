"""Project exceptions. Callers catch `GovagentError`; nothing is swallowed silently."""


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
