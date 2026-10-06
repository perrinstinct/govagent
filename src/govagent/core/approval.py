"""Which proposals may be approved without a human looking at them (CLAUDE.md rules 3 and 5).

Approval lives outside the agent graph: the graph only produces a report. A proposal is
auto-approvable only if it was verified (RESOLVED) and its rule is not breaking. Breaking-ness
comes from rules_meta.yaml, copied onto the proposal by the agent, never from the model.
"""

from govagent.domain.models import AnalysisReport, FixStatus


def auto_approvable_ids(report: AnalysisReport) -> list[str]:
    """Proposal ids that `--yes-non-breaking` may approve, in application order."""
    return [
        outcome.proposal.id
        for outcome in report.outcomes
        if outcome.status is FixStatus.RESOLVED
        and outcome.proposal is not None
        and not outcome.proposal.breaking
    ]
