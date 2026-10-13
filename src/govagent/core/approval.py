"""Human approval, outside the agent graph (CLAUDE.md rules 3 and 5; docs/SPEC.md §7).

The graph only produces a report. Approval is a separate, deterministic step:
- `auto_approvable_ids`: what `--yes-non-breaking` may approve. Only verified (RESOLVED),
  non-breaking proposals; breaking-ness comes from rules_meta.yaml, never from the model.
- `apply_approved`: re-applies the approved proposals, in their original order, on the
  original spec, then re-lints. The agent verified each proposal on top of all the previous
  ones; a subset can break that chain (a summary added under a path whose rename was not
  approved), so the subset is checked again and rejected with an explicit error.
- `proposal_diffs`: a unified diff per proposal, for review.
"""

import difflib
from collections.abc import Collection

from pydantic import BaseModel

from govagent.core.integrity import dangling_required
from govagent.core.patching import apply_patch
from govagent.core.spec_io import SpecDocument, dump_spec, load_spec
from govagent.core.verification import BLOCKING, verify
from govagent.domain.errors import ApprovalError, PatchError, PointerError
from govagent.domain.models import AnalysisReport, FixProposal, FixStatus, PatchOp, Violation
from govagent.domain.ports import Linter


class AppliedFixes(BaseModel, frozen=True):
    spec_text: str
    applied: list[str]  # proposal ids, in application order
    remaining: list[Violation]  # lint of spec_text


def auto_approvable_ids(report: AnalysisReport) -> list[str]:
    """Proposal ids that `--yes-non-breaking` may approve, in application order."""
    return [proposal.id for proposal in resolved_proposals(report) if not proposal.breaking]


def resolved_proposals(report: AnalysisReport) -> list[FixProposal]:
    """Verified proposals, in the order the agent applied them."""
    return [
        outcome.proposal
        for outcome in report.outcomes
        if outcome.status is FixStatus.RESOLVED and outcome.proposal is not None
    ]


def apply_approved(
    original_text: str, report: AnalysisReport, approved_ids: Collection[str], linter: Linter
) -> AppliedFixes:
    """Apply exactly the approved proposals. Raises ApprovalError if they do not hold together."""
    proposals = resolved_proposals(report)
    known = {proposal.id for proposal in proposals}
    unknown = sorted(set(approved_ids) - known)
    if unknown:
        raise ApprovalError(f"not a resolved proposal of this analysis: {', '.join(unknown)}")

    spec = load_spec(original_text)
    root = spec.root
    applied: list[str] = []
    skipped: list[str] = []
    ops: list[PatchOp] = []
    for proposal in proposals:
        if proposal.id not in approved_ids:
            skipped.append(proposal.id)
            continue
        try:
            root = apply_patch(root, proposal.ops)
        except (PatchError, PointerError) as exc:
            raise ApprovalError(
                f"{proposal.id} no longer applies without the unapproved "
                f"{', '.join(skipped)}: {exc}",
                proposal_id=proposal.id,
            ) from exc
        applied.append(proposal.id)
        ops.extend(proposal.ops)

    dangling = dangling_required(root) - dangling_required(spec.root)
    if dangling:
        names = ", ".join(f"{pointer}/required {name!r}" for pointer, name in sorted(dangling))
        raise ApprovalError(f"the approved fixes leave dangling required entries: {names}")
    patched_text = dump_spec(SpecDocument(root, spec.style))
    load_spec(patched_text)  # still a valid OpenAPI document
    after = linter.lint(patched_text)
    result = verify(linter.lint(original_text), after, (), ops)
    introduced = [v for v in result.introduced if v.severity in BLOCKING]
    if introduced:
        found = "; ".join(f"{v.rule_id} at {v.pointer or '/'}" for v in introduced)
        raise ApprovalError(f"the approved subset introduces violations: {found}")
    return AppliedFixes(spec_text=patched_text, applied=applied, remaining=after)


def apply_approvable(
    original_text: str, report: AnalysisReport, candidate_ids: Collection[str], linter: Linter
) -> tuple[AppliedFixes, list[str]]:
    """Best effort for automatic approval: drop the candidates that only apply on top of an
    unapproved proposal, apply the rest. Returns the result and the dropped ids."""
    remaining = list(candidate_ids)
    dropped: list[str] = []
    while True:
        try:
            return apply_approved(original_text, report, remaining, linter), dropped
        except ApprovalError as exc:
            if exc.proposal_id is None or exc.proposal_id not in remaining:
                raise
            remaining.remove(exc.proposal_id)
            dropped.append(exc.proposal_id)


def proposal_diffs(original_text: str, report: AnalysisReport, context: int = 2) -> dict[str, str]:
    """proposal id -> unified diff of that proposal, on top of the ones applied before it."""
    spec = load_spec(original_text)
    root, before = spec.root, original_text
    diffs: dict[str, str] = {}
    for proposal in resolved_proposals(report):
        root = apply_patch(root, proposal.ops)
        after = dump_spec(SpecDocument(root, spec.style))
        diffs[proposal.id] = unified_diff(before, after, context)
        before = after
    return diffs


def unified_diff(before: str, after: str, context: int = 2) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="a/spec",
        tofile="b/spec",
        n=context,
    )
    return "".join(lines)
