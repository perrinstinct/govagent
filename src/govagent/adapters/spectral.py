"""Linter port implemented with the Spectral CLI (subprocess)."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from govagent.core.pointers import format_pointer
from govagent.domain.errors import InvalidSpecError, LinterError
from govagent.domain.models import Severity, Violation

_SEVERITIES = {0: Severity.ERROR, 1: Severity.WARN, 2: Severity.INFO, 3: Severity.HINT}
# Spectral reports these as ordinary results, but they mean the document itself is unusable.
_DOCUMENT_ERRORS = frozenset({"parser", "unrecognized-format", "invalid-ref"})


class SpectralLinter:
    def __init__(self, spectral_bin: str, ruleset_path: Path, timeout_seconds: float = 60) -> None:
        self._spectral_bin = spectral_bin
        self._ruleset_path = ruleset_path
        self._timeout_seconds = timeout_seconds

    def lint(self, spec_text: str) -> list[Violation]:
        stdout = self._run(spec_text)
        try:
            results = cast(list[dict[str, Any]], json.loads(stdout))
        except json.JSONDecodeError as exc:
            raise LinterError(f"spectral returned invalid JSON: {stdout[:200]!r}") from exc

        document_errors = [r for r in results if r["code"] in _DOCUMENT_ERRORS]
        if document_errors:
            details = "; ".join(f"{r['code']}: {r['message']}" for r in document_errors)
            raise InvalidSpecError(f"spectral cannot lint this document: {details}")

        violations: list[Violation] = []
        seen: set[str] = set()
        for result in results:
            violation = Violation(
                rule_id=result["code"],
                severity=_SEVERITIES[result["severity"]],
                pointer=format_pointer(result["path"]),
                message=result["message"],
            )
            if violation.fingerprint not in seen:
                seen.add(violation.fingerprint)
                violations.append(violation)
        return violations

    def _run(self, spec_text: str) -> str:
        suffix = ".json" if spec_text.lstrip().startswith("{") else ".yaml"
        with tempfile.TemporaryDirectory(prefix="govagent-") as tmp:
            spec_file = Path(tmp) / f"spec{suffix}"
            spec_file.write_text(spec_text)
            command = [
                self._spectral_bin,
                "lint",
                "--quiet",
                "--format",
                "json",
                "--ruleset",
                str(self._ruleset_path),
                str(spec_file),
            ]
            try:
                # Exit code 1 only means "violations found": stdout is the source of truth.
                completed = subprocess.run(  # noqa: S603
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self._timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise LinterError(f"spectral binary not found: {self._spectral_bin}") from exc
            except subprocess.TimeoutExpired as exc:
                raise LinterError(f"spectral timed out after {self._timeout_seconds}s") from exc
        if not completed.stdout.strip():
            raise LinterError(
                f"spectral produced no output (exit {completed.returncode}): "
                f"{completed.stderr.strip()[:500]}"
            )
        return completed.stdout
