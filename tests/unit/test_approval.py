"""Invariant (CLAUDE.md): a fix whose rule is `breaking: true` is never auto-approved."""

from pathlib import Path

import pytest

from govagent.core.approval import auto_approvable_ids
from govagent.core.rules_meta import parse_rules_meta
from govagent.domain.models import (
    AnalysisReport,
    FixOutcome,
    FixProposal,
    FixStatus,
    RuleMeta,
    Usage,
)

RULES_META = parse_rules_meta(
    (Path(__file__).resolve().parents[2] / "rulesets" / "rules_meta.yaml").read_text()
)


def outcome(meta: RuleMeta, status: FixStatus = FixStatus.RESOLVED) -> FixOutcome:
    proposal = FixProposal(
        id=f"{meta.rule_id}-1",
        group_id=meta.rule_id,
        ops=[],
        rationale="r",
        breaking=meta.breaking,  # what the agent does: copied from the rule metadata
        attempt=1,
    )
    return FixOutcome(
        group_id=meta.rule_id,
        rule_id=meta.rule_id,
        scope_pointer="/info",
        status=status,
        proposal=proposal,
        attempts=1,
    )


def report(*outcomes: FixOutcome) -> AnalysisReport:
    return AnalysisReport(
        analysis_id="a",
        spec_sha256="s",
        initial_violations=[],
        outcomes=list(outcomes),
        final_violations=[],
        usage=Usage(),
        duration_ms=0,
    )


@pytest.mark.parametrize("rule_id", sorted(RULES_META))
def test_resolved_fix_is_auto_approvable_only_if_its_rule_is_not_breaking(rule_id: str) -> None:
    meta = RULES_META[rule_id]

    approved = auto_approvable_ids(report(outcome(meta)))

    assert approved == ([] if meta.breaking else [f"{rule_id}-1"])


def test_the_ruleset_has_breaking_rules_so_the_invariant_is_exercised() -> None:
    assert any(meta.breaking for meta in RULES_META.values())
    assert any(not meta.breaking for meta in RULES_META.values())


@pytest.mark.parametrize(
    "status", [s for s in FixStatus if s is not FixStatus.RESOLVED], ids=lambda s: s.value
)
def test_unresolved_outcomes_are_never_auto_approvable(status: FixStatus) -> None:
    non_breaking = next(meta for meta in RULES_META.values() if not meta.breaking)

    assert auto_approvable_ids(report(outcome(non_breaking, status))) == []


def test_order_of_application_is_kept() -> None:
    non_breaking = [meta for meta in RULES_META.values() if not meta.breaking][:2]

    approved = auto_approvable_ids(report(*(outcome(meta) for meta in non_breaking)))

    assert approved == [f"{meta.rule_id}-1" for meta in non_breaking]
