"""Ports implemented by adapters (docs/SPEC.md §3). FixModel and GitHost arrive in M3 and M6."""

from typing import Protocol

from govagent.domain.models import Violation


class Linter(Protocol):
    def lint(self, spec_text: str) -> list[Violation]:
        """Return every violation found in the spec. Raises LinterError if the linter crashes."""
        ...
