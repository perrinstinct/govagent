# ADR 003: Human approval lives outside the agent graph

- Status: accepted
- Recorded: 2026-10-19 (decided in M3, completed in M5)

## Context

Fixes change an API contract that other teams depend on. A human must decide what ships, and the
decision must not depend on how the model behaved during the run.

## Decision

The LangGraph graph ends with a report (`AnalysisReport`): verified proposals, failures, items
that need a human, usage and cost. Approval is a separate, deterministic step:

- `core/approval.apply_approved` re-applies only the approved proposals, in their original order,
  on the original spec, then re-lints. A subset that does not hold together (a summary added under
  a path whose rename was not approved) is rejected with an explicit error naming the dependency.
- `--yes-non-breaking` uses a best-effort variant that drops fixes depending on an unapproved one
  and says so.
- The MCP server mirrors this split: `propose_fixes` stores the analysis, `apply_fixes` and
  `open_pull_request` take the approved ids.

## Alternatives considered

- **Human-in-the-loop interrupts inside the graph** (LangGraph `interrupt`): ties approval to a
  live run and its checkpoint storage; harder to expose through a stateless MCP server.
- **Apply everything, review in the PR**: pushes the decision to code review and mixes safe and
  breaking changes in one diff.

## Consequences

- The graph stays a pure "propose and verify" machine, testable with a scripted model.
- Approval can happen later and elsewhere (CLI, MCP client, PR), within the analysis TTL.
- The subset re-check costs a couple of Spectral runs, no model call.
