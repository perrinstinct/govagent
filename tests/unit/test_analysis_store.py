import pytest

from govagent.domain.models import AnalysisReport, Usage
from govagent.interfaces.analysis_store import AnalysisStore, UnknownAnalysisError


def report(analysis_id: str) -> AnalysisReport:
    return AnalysisReport(
        analysis_id=analysis_id,
        spec_sha256="s",
        initial_violations=[],
        outcomes=[],
        final_violations=[],
        usage=Usage(),
        duration_ms=0,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_stored_analysis_is_returned_until_it_expires() -> None:
    clock = Clock()
    store = AnalysisStore(ttl_seconds=3600, clock=clock)
    store.put("openapi: 3.0.3\n", report("a1"))

    clock.now += 3599
    assert store.get("a1").spec_text == "openapi: 3.0.3\n"

    clock.now += 1
    with pytest.raises(UnknownAnalysisError, match="expired"):
        store.get("a1")


def test_unknown_id_is_an_error() -> None:
    with pytest.raises(UnknownAnalysisError, match="propose_fixes again"):
        AnalysisStore(ttl_seconds=60).get("nope")
