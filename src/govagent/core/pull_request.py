"""Open a pull request with the approved fixes (docs/SPEC.md §7). Deterministic, outside the graph.

Guards, in order: the repository must be allowlisted (blast radius), the file on the base
branch must still be the analyzed one (no blind patching), and the approved subset must hold
together (`apply_approved`). Only then is a branch created and the PR opened.
"""

import json
from collections.abc import Collection, Sequence

from pydantic import BaseModel

from govagent.core.approval import AppliedFixes, apply_approved, resolved_proposals
from govagent.domain.errors import ApprovalError, RepoNotAllowedError, StaleSpecError
from govagent.domain.models import AnalysisReport, FixStatus, Violation
from govagent.domain.ports import GitHost, Linter

REPO_URL = "https://github.com/perrinstinct/govagent"


class PullRequest(BaseModel, frozen=True):
    url: str
    branch: str
    applied: list[str]
    remaining: list[Violation]


def check_repo_allowed(repo: str, allowlist: Sequence[str]) -> None:
    """GitHub names are case-insensitive. Raises RepoNotAllowedError."""
    if repo.lower() not in {allowed.lower() for allowed in allowlist}:
        raise RepoNotAllowedError(
            f"repository {repo!r} is not in GOVAGENT_REPO_ALLOWLIST "
            f"({', '.join(allowlist) or 'empty'})"
        )


def open_fix_pull_request(
    host: GitHost,
    *,
    repo: str,
    path: str,
    base: str,
    original_text: str,
    report: AnalysisReport,
    approved_ids: Collection[str],
    linter: Linter,
    allowlist: Sequence[str],
) -> PullRequest:
    check_repo_allowed(repo, allowlist)
    current_text, blob_sha = host.get_file(repo, path, base)
    if current_text != original_text:
        raise StaleSpecError(
            f"{repo}:{path} on {base} changed since it was analyzed: run the analysis again"
        )
    applied = apply_approved(original_text, report, approved_ids, linter)
    if not applied.applied:
        raise ApprovalError("no fix approved: nothing to put in a pull request")

    branch = f"govagent/{report.analysis_id[:8]}"
    count = len(applied.applied)
    host.create_branch(repo, branch, base)
    host.update_file(
        repo,
        path,
        branch,
        applied.spec_text,
        blob_sha,
        f"Fix {count} API governance violation{'s' if count > 1 else ''} in {path}",
    )
    url = host.open_pr(
        repo,
        head=branch,
        base=base,
        title=f"API governance: {count} verified fix{'es' if count > 1 else ''} for {path}",
        body=render_pr_body(report, applied, path),
    )
    return PullRequest(url=url, branch=branch, applied=applied.applied, remaining=applied.remaining)


def render_pr_body(report: AnalysisReport, applied: AppliedFixes, path: str) -> str:
    outcomes = {o.proposal.id: o for o in report.outcomes if o.proposal is not None}
    verified = resolved_proposals(report)
    approved = [p for p in verified if p.id in applied.applied]
    not_approved = [p for p in verified if p.id not in applied.applied]
    failed = [
        o
        for o in report.outcomes
        if o.status in (FixStatus.FAILED, FixStatus.REJECTED_SCOPE, FixStatus.BUDGET_EXCEEDED)
    ]
    human = [o for o in report.outcomes if o.status is FixStatus.NEEDS_HUMAN]
    breaking = sum(p.breaking for p in approved)

    lines = [
        f"govagent found {len(report.initial_violations)} API governance violation(s) in "
        f"`{path}` and verified each fix below by re-linting the spec.",
        "",
        "| | Count |",
        "|---|---|",
        f"| Fixes in this PR | {len(approved)} ({breaking} breaking) |",
        f"| Verified fixes not approved | {len(not_approved)} |",
        f"| Not fixed automatically | {len(failed)} |",
        f"| Need human input | {len(human)} |",
        f"| Violations left after this PR | {len(applied.remaining)} |",
        "",
        "## Fixes",
    ]
    for proposal in approved:
        outcome = outcomes[proposal.id]
        lines += ["", f"### `{outcome.rule_id}` at `{outcome.scope_pointer or '/'}`"]
        if proposal.breaking:
            lines += [
                "",
                "> **Breaking change**: this fix changes the API contract. "
                "Clients may need an update.",
            ]
        ops = json.dumps([op.model_dump(by_alias=True, exclude_none=True) for op in proposal.ops])
        lines += [
            "",
            proposal.rationale,
            "",
            "<details><summary>JSON Patch</summary>",
            "",
            "```json",
            ops,
            "```",
            "",
            "</details>",
        ]
    if human:
        lines += ["", "## Needs human input", ""]
        lines += [f"- `{o.rule_id}` at `{o.scope_pointer or '/'}`" for o in human]
    if applied.remaining:
        lines += ["", "## Violations left after this PR", ""]
        lines += [
            f"- {v.severity.value} `{v.rule_id}` at `{v.pointer or '/'}`: {v.message}"
            for v in applied.remaining
        ]
    usage = report.usage
    lines += [
        "",
        "---",
        f"Analysis `{report.analysis_id[:8]}` · {usage.llm_calls} model call(s) · "
        f"${usage.cost_usd:.4f} · opened by [govagent]({REPO_URL}) after human approval",
    ]
    return "\n".join(lines) + "\n"
