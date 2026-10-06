"""Ports implemented by adapters (docs/SPEC.md §3). GitHost arrives in M6."""

from typing import Protocol

from govagent.domain.models import FixRequest, LLMFixOutput, Usage, Violation


class Linter(Protocol):
    def lint(self, spec_text: str) -> list[Violation]:
        """Return every violation found in the spec. Raises LinterError if the linter crashes."""
        ...


class FixModel(Protocol):
    def propose(self, request: FixRequest) -> tuple[LLMFixOutput, Usage]:
        """One model call. `Usage` covers this call only (llm_calls=1, tokens, cost).

        Raises ModelOutputError (carrying the call's usage) when the output does not match the
        schema, and LLMError when the provider fails.
        """
        ...
