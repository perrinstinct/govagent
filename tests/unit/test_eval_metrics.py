import pytest

from evals.build_dataset import InjectedViolation
from evals.metrics import CaseResult, OutcomeRecord, render_markdown, summarize
from govagent.domain.models import FixStatus, Usage

SUMMARY = InjectedViolation(rule_id="gov-operation-summary", pointer="/paths/~1a/get")
TAGS = InjectedViolation(rule_id="gov-operation-tags", pointer="/paths/~1a/get")
HTTPS = InjectedViolation(rule_id="gov-servers-https", pointer="/servers/0/url")


def result(
    injected: list[InjectedViolation],
    final: list[InjectedViolation],
    *,
    attempts: int = 1,
    cost: float = 0.01,
    latency: int = 1000,
    valid: bool = True,
    error: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id="c",
        seed="s.yaml",
        kind="single",
        injected=injected,
        initial=[] if error else injected,
        final=final,
        outcomes=[
            OutcomeRecord(rule_id=v.rule_id, status=FixStatus.RESOLVED, attempts=attempts)
            for v in injected
        ],
        final_valid=valid,
        usage=Usage(llm_calls=attempts, cost_usd=cost),
        latency_ms=latency,
        error=error,
    )


def test_fix_rate_counts_per_rule_so_moved_pointers_do_not_matter() -> None:
    moved = SUMMARY.model_copy(update={"pointer": "/paths/~1b/get"})  # same rule, new pointer

    r = result([SUMMARY, TAGS], [moved])

    assert r.fixed_by_rule() == {"gov-operation-summary": (1, 0), "gov-operation-tags": (1, 1)}
    assert not r.regressed


def test_violation_of_a_rule_that_was_not_injected_is_a_regression() -> None:
    assert result([SUMMARY], [HTTPS]).regressed


def test_summary_aggregates_rates_cost_and_latency() -> None:
    results = [
        result([SUMMARY], [], attempts=1, cost=0.01, latency=1000),
        result([SUMMARY, TAGS], [TAGS], attempts=2, cost=0.03, latency=3000),
        result([HTTPS], [HTTPS, SUMMARY], attempts=3, cost=0.02, latency=2000, valid=False),
    ]

    summary = summarize("model", "smoke", results, skipped=1)

    assert summary.cases == 3
    assert summary.skipped == 1
    assert summary.sanity_rate == 1.0
    assert (summary.injected, summary.fixed) == (4, 2)
    assert summary.fix_rate == 0.5
    assert summary.regression_rate == pytest.approx(1 / 3)
    assert summary.valid_spec_rate == pytest.approx(2 / 3)
    assert summary.total_cost_usd == pytest.approx(0.06)
    assert summary.cost_per_case_usd == pytest.approx(0.02)
    assert (summary.latency_p50_ms, summary.latency_p95_ms) == (2000, 3000)
    by_rule = {stats.rule_id: stats for stats in summary.per_rule}
    assert by_rule["gov-operation-summary"].fix_rate == 1.0
    assert by_rule["gov-servers-https"].fix_rate == 0.0


def test_errored_case_counts_as_unfixed_but_not_as_regression() -> None:
    summary = summarize("m", "smoke", [result([SUMMARY], [SUMMARY], error="LLMError: x")], 0)

    assert summary.errors == 1
    assert summary.fix_rate == 0.0
    assert summary.regression_rate == 0.0


def test_markdown_report_has_headline_metrics_and_per_rule_table() -> None:
    text = render_markdown(summarize("model-x", "full", [result([SUMMARY], [])], 0))

    assert "# Eval results: `model-x` (full)" in text
    assert "**Fix rate** (fixed / injected) | **100.0%** (1/1)" in text
    assert "| `gov-operation-summary` | 1 | 1 | 100% | 1.00 |" in text
