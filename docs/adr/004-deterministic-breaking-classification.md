# ADR 004: Breaking-ness and fix class are deterministic

- Status: accepted
- Recorded: 2026-10-19 (decided in M0, enforced in M3)

## Context

Whether a fix breaks clients (renaming a path, an `operationId`, a schema property) is the most
important fact for the reviewer. It must not depend on what the model claims about its own
change.

## Decision

Each rule has metadata in `rulesets/rules_meta.yaml`: fix class (`auto` or `needs_human_input`),
`breaking`, write scope and fixing guidance. The agent copies `breaking` from the rule onto every
proposal; the model's structured output has no field for it. The model may *downgrade* a fix to
`needs_human_input`, never upgrade one: `needs_human_input` rules never reach the model.

Breaking fixes are never auto-approved: `core/approval.auto_approvable_ids` only returns verified,
non-breaking proposals. An invariant test checks this for every rule of the ruleset.

## Alternatives considered

- **Ask the model whether its fix is breaking**: unverifiable, and the one answer that must
  never be wrong.
- **Diff-based breaking-change detection** (e.g. oasdiff between versions): more precise for
  arbitrary changes, but a heavier dependency; on the roadmap.

## Consequences

- The demo pull request shows it: three breaking fixes were verified but left out by
  `--yes-non-breaking`, and the PR body lists them as "verified fixes not approved".
- Classification is per rule, not per change: a rule marked breaking is treated as breaking even
  when a particular fix would be harmless. Conservative by design.
