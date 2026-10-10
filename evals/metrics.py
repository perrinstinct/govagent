"""Per-case results and aggregate metrics (docs/SPEC.md §9, M4).

Seeds are lint-clean, so every violation in a case is either injected or caused by the agent:
- fixed (per rule) = injected - remaining. Counting per rule rather than per fingerprint keeps
  the metric correct when a fix moves pointers (e.g. a path rename);
- a regression is a final violation of a rule that was not injected, or more violations of a
  rule than were injected.
Cases that errored count their injected violations as not fixed; they are excluded from the
regression and valid-spec rates, and reported separately.
"""

import math
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel

from evals.build_dataset import InjectedViolation
from govagent.domain.models import FixStatus, Usage


class OutcomeRecord(BaseModel, frozen=True):
    rule_id: str
    status: FixStatus
    attempts: int


class CaseResult(BaseModel, frozen=True):
    case_id: str
    seed: str
    kind: str
    injected: list[InjectedViolation]
    initial: list[InjectedViolation]  # what the linter reported before the agent ran
    final: list[InjectedViolation]  # what is left after every RESOLVED fix was applied
    outcomes: list[OutcomeRecord]
    final_valid: bool
    usage: Usage
    latency_ms: int
    error: str | None = None

    @property
    def sane(self) -> bool:
        """Dataset sanity: the linter detected every injected violation."""
        initial = {v.fingerprint for v in self.initial}
        return all(v.fingerprint in initial for v in self.injected)

    def fixed_by_rule(self) -> dict[str, tuple[int, int]]:
        """rule_id -> (injected, fixed)."""
        injected = Counter(v.rule_id for v in self.injected)
        remaining = Counter(v.rule_id for v in self.final)
        if self.error is not None:
            return {rule: (count, 0) for rule, count in injected.items()}
        return {rule: (count, max(0, count - remaining[rule])) for rule, count in injected.items()}

    @property
    def regressed(self) -> bool:
        injected = Counter(v.rule_id for v in self.injected)
        remaining = Counter(v.rule_id for v in self.final)
        return any(count > injected[rule] for rule, count in remaining.items())


class RuleStats(BaseModel, frozen=True):
    rule_id: str
    injected: int
    fixed: int
    fix_rate: float
    mean_attempts: float | None  # over this rule's outcomes that called the model


class Summary(BaseModel, frozen=True):
    model_id: str
    subset: str
    cases: int
    errors: int
    skipped: int  # not run: global cost cap reached
    sanity_rate: float
    injected: int
    fixed: int
    fix_rate: float
    regression_rate: float
    valid_spec_rate: float
    mean_attempts: float | None
    llm_calls: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: float
    cost_per_case_usd: float
    latency_p50_ms: int
    latency_p95_ms: int
    per_rule: list[RuleStats]


def summarize(model_id: str, subset: str, results: Sequence[CaseResult], skipped: int) -> Summary:
    completed = [r for r in results if r.error is None]
    per_rule_counts: dict[str, list[int]] = {}
    for result in results:
        for rule, (injected, fixed) in result.fixed_by_rule().items():
            counts = per_rule_counts.setdefault(rule, [0, 0])
            counts[0] += injected
            counts[1] += fixed
    attempts_by_rule: dict[str, list[int]] = {}
    for result in completed:
        for outcome in result.outcomes:
            if outcome.attempts > 0:
                attempts_by_rule.setdefault(outcome.rule_id, []).append(outcome.attempts)

    per_rule = [
        RuleStats(
            rule_id=rule,
            injected=injected,
            fixed=fixed,
            fix_rate=_ratio(fixed, injected),
            mean_attempts=_mean(attempts_by_rule.get(rule, [])),
        )
        for rule, (injected, fixed) in sorted(per_rule_counts.items())
    ]
    injected_total = sum(stats.injected for stats in per_rule)
    fixed_total = sum(stats.fixed for stats in per_rule)
    latencies = sorted(r.latency_ms for r in results)
    total_cost = sum(r.usage.cost_usd for r in results)
    return Summary(
        model_id=model_id,
        subset=subset,
        cases=len(results),
        errors=len(results) - len(completed),
        skipped=skipped,
        sanity_rate=_ratio(sum(r.sane for r in results), len(results)),
        injected=injected_total,
        fixed=fixed_total,
        fix_rate=_ratio(fixed_total, injected_total),
        regression_rate=_ratio(sum(r.regressed for r in completed), len(completed)),
        valid_spec_rate=_ratio(sum(r.final_valid for r in completed), len(completed)),
        mean_attempts=_mean([a for values in attempts_by_rule.values() for a in values]),
        llm_calls=sum(r.usage.llm_calls for r in results),
        input_tokens=sum(r.usage.input_tokens for r in results),
        output_tokens=sum(r.usage.output_tokens for r in results),
        total_cost_usd=total_cost,
        cost_per_case_usd=total_cost / len(results) if results else 0.0,
        latency_p50_ms=_percentile(latencies, 50),
        latency_p95_ms=_percentile(latencies, 95),
        per_rule=per_rule,
    )


def render_markdown(summary: Summary) -> str:
    s = summary
    attempts = "-" if s.mean_attempts is None else f"{s.mean_attempts:.2f}"
    lines = [
        f"# Eval results: `{s.model_id}` ({s.subset})",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Cases | {s.cases} ({s.errors} errors, {s.skipped} skipped) |",
        f"| Dataset sanity (injected violations detected) | {s.sanity_rate:.0%} |",
        f"| **Fix rate** (fixed / injected) | **{s.fix_rate:.1%}** ({s.fixed}/{s.injected}) |",
        f"| **Regression rate** (cases with new violations) | **{s.regression_rate:.1%}** |",
        f"| Valid spec rate | {s.valid_spec_rate:.1%} |",
        f"| Mean attempts per fix | {attempts} |",
        f"| LLM calls | {s.llm_calls} ({s.input_tokens} in / {s.output_tokens} out tokens) |",
        f"| **Cost per case** | **${s.cost_per_case_usd:.4f}** (total ${s.total_cost_usd:.2f}) |",
        f"| Latency p50 / p95 | {s.latency_p50_ms / 1000:.1f} s / "
        f"{s.latency_p95_ms / 1000:.1f} s |",
        "",
        "| Rule | Injected | Fixed | Fix rate | Mean attempts |",
        "|---|---|---|---|---|",
    ]
    for rule in s.per_rule:
        rule_attempts = "-" if rule.mean_attempts is None else f"{rule.mean_attempts:.2f}"
        lines.append(
            f"| `{rule.rule_id}` | {rule.injected} | {rule.fixed} | {rule.fix_rate:.0%} "
            f"| {rule_attempts} |"
        )
    return "\n".join(lines) + "\n"


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: Sequence[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(sorted_values: Sequence[int], percent: int) -> int:
    """Nearest-rank percentile."""
    if not sorted_values:
        return 0
    rank = math.ceil(percent / 100 * len(sorted_values))
    return sorted_values[max(rank, 1) - 1]
