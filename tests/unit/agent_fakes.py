"""Test doubles for the agent: a pure-Python linter and a scripted fix model."""

import re
from collections.abc import Callable
from typing import Any

from govagent.core.openapi import HTTP_METHODS
from govagent.core.pointers import format_pointer
from govagent.core.spec_io import load_spec
from govagent.domain.errors import ModelOutputError
from govagent.domain.models import (
    FixRequest,
    LLMFixOutput,
    PatchOp,
    Severity,
    Usage,
    Violation,
)

CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
KEBAB_PATH = re.compile(r"^(/([a-z0-9]+(-[a-z0-9]+)*|\{[^/{}]+\}))*/?$")


class FakeLinter:
    """Mimics four rules of the real ruleset, in Spectral's document order."""

    def __init__(self) -> None:
        self.calls = 0

    def lint(self, spec_text: str) -> list[Violation]:
        self.calls += 1
        root: Any = load_spec(spec_text, validate_openapi=False).root
        found: list[Violation] = []
        if not root.get("info", {}).get("contact", {}).get("email"):
            found.append(_v("gov-info-contact", ["info"], Severity.WARN))
        for path, item in root.get("paths", {}).items():
            if not KEBAB_PATH.match(path):
                found.append(_v("gov-path-kebab", ["paths", path], Severity.ERROR))
            for method, operation in item.items():
                if method not in HTTP_METHODS:
                    continue
                if not operation.get("summary"):
                    found.append(_v("gov-operation-summary", ["paths", path, method]))
                operation_id = operation.get("operationId")
                if operation_id and not CAMEL.match(operation_id):
                    pointer = ["paths", path, method, "operationId"]
                    found.append(_v("gov-operation-id-camel", pointer))
        return found


def _v(rule_id: str, segments: list[str], severity: Severity = Severity.WARN) -> Violation:
    return Violation(
        rule_id=rule_id, severity=severity, pointer=format_pointer(segments), message=rule_id
    )


Script = LLMFixOutput | ModelOutputError | Callable[[FixRequest], LLMFixOutput]


class ScriptedModel:
    """Returns scripted answers in order and records every request it receives."""

    def __init__(self, *script: Script, cost_per_call: float = 0.001) -> None:
        self._script = list(script)
        self.requests: list[FixRequest] = []
        self._cost = cost_per_call

    def propose(self, request: FixRequest) -> tuple[LLMFixOutput, Usage]:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"unexpected model call: {request.group_id} #{request.attempt}")
        answer = self._script.pop(0)
        usage = Usage(llm_calls=1, input_tokens=1000, output_tokens=100, cost_usd=self._cost)
        if isinstance(answer, ModelOutputError):
            raise ModelOutputError(str(answer), usage)
        output = answer(request) if callable(answer) else answer
        return output, usage

    @property
    def remaining(self) -> int:
        return len(self._script)


def fix(*ops: dict[str, Any], rationale: str = "scripted fix") -> LLMFixOutput:
    return LLMFixOutput(
        ops=[PatchOp.model_validate(op) for op in ops],
        rationale=rationale,
        needs_human_input=False,
    )


def human() -> LLMFixOutput:
    return LLMFixOutput(ops=[], rationale="needs business input", needs_human_input=True)


def schema_error() -> ModelOutputError:
    return ModelOutputError("tool call missing 'ops'", Usage())


class FakeGitHost:
    """In-memory GitHost: one file per (repo, path) on `main`, records every call."""

    def __init__(self, files: dict[tuple[str, str], str]) -> None:
        self.files = dict(files)
        self.calls: list[tuple[str, ...]] = []
        self.commits: dict[str, str] = {}  # branch -> committed text
        self.pull_requests: list[dict[str, str]] = []

    def get_file(self, repo: str, path: str, ref: str) -> tuple[str, str]:
        self.calls.append(("get_file", repo, path, ref))
        text = self.files[(repo, path)]
        return text, f"sha-{len(text)}"

    def create_branch(self, repo: str, branch: str, from_ref: str) -> None:
        self.calls.append(("create_branch", repo, branch, from_ref))

    def update_file(
        self, repo: str, path: str, branch: str, text: str, sha: str, message: str
    ) -> None:
        self.calls.append(("update_file", repo, path, branch, sha, message))
        self.commits[branch] = text

    def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        self.calls.append(("open_pr", repo, head, base, title))
        self.pull_requests.append({"title": title, "body": body, "head": head, "base": base})
        return f"https://github.com/{repo}/pull/{len(self.pull_requests)}"
