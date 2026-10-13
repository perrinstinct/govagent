"""In-memory store of analyses between `propose_fixes` and `apply_fixes` (docs/SPEC.md §8).

Single process only: entries live in this task's memory and expire after the TTL. Documented
limitation; a shared store (DynamoDB) is the V2 path when the service runs several tasks.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from govagent.domain.errors import GovagentError
from govagent.domain.models import AnalysisReport


class UnknownAnalysisError(GovagentError):
    """The analysis id does not exist or has expired."""


@dataclass(frozen=True)
class StoredAnalysis:
    spec_text: str  # the original spec the proposals apply to
    report: AnalysisReport
    expires_at: float


class AnalysisStore:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, StoredAnalysis] = {}
        self._lock = threading.Lock()  # tools run in worker threads

    def put(self, spec_text: str, report: AnalysisReport) -> StoredAnalysis:
        stored = StoredAnalysis(spec_text, report, self._clock() + self._ttl)
        with self._lock:
            self._prune()
            self._entries[report.analysis_id] = stored
        return stored

    def get(self, analysis_id: str) -> StoredAnalysis:
        with self._lock:
            self._prune()
            stored = self._entries.get(analysis_id)
        if stored is None:
            raise UnknownAnalysisError(
                f"unknown or expired analysis {analysis_id!r}: run propose_fixes again"
            )
        return stored

    def _prune(self) -> None:
        now = self._clock()
        for analysis_id in [k for k, v in self._entries.items() if v.expires_at <= now]:
            del self._entries[analysis_id]
