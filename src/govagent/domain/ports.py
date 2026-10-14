"""Ports implemented by adapters (docs/SPEC.md §3)."""

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


class GitHost(Protocol):
    """The few repository operations needed to open a fix pull request."""

    def get_file(self, repo: str, path: str, ref: str) -> tuple[str, str]:
        """(text, blob sha) of `path` at `ref`. `repo` is `OWNER/NAME`."""
        ...

    def create_branch(self, repo: str, branch: str, from_ref: str) -> None:
        """Create `branch` at the head commit of `from_ref`."""
        ...

    def update_file(
        self, repo: str, path: str, branch: str, text: str, sha: str, message: str
    ) -> None:
        """Commit `text` to `path` on `branch`; `sha` is the blob being replaced."""
        ...

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        """Open a pull request and return its URL."""
        ...
