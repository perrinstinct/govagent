# ADR 002: JSON Patch over full rewrite

- Status: accepted
- Recorded: 2026-10-19 (decided in M2)

## Context

Asking a model to return the corrected spec produces a full rewrite: comments lost, keys
reordered, quoting changed, and silent edits nobody asked for. On a 3,000-line spec the diff is
unreviewable, and large specs do not even fit in one response.

## Decision

The model returns RFC 6902 JSON Patch operations, limited to `add`, `remove`, `replace` and
`move`, with absolute RFC 6901 pointers. They are:

1. **checked against the rule's scope** before anything is applied (`core/scope_guard.py`):
   an operation outside the allowed pointers is rejected deterministically;
2. **applied on ruamel.yaml round-trip nodes** (`core/patching.py`), so comments, key order and
   quotes survive; renames keep their position, well-known keys are inserted at their
   conventional place;
3. **all or nothing**: the patch is applied to a copy.

## Alternatives considered

- **Full rewrite + diff**: the diff is only as small as the model is careful; formatting noise
  hides real changes.
- **Free-form edit instructions** ("add a summary to POST /pets"): needs a second interpreter,
  ambiguous on renames.

## Consequences

- Diffs are minimal: the M1 fix changed one line; the demo pull request
  ([govagent-demo#1](https://github.com/perrinstinct/govagent-demo/pull/1)) is +6 −1 for four fixes.
- The scope guard is a real safety boundary, not a prompt instruction.
- The model's typical mistake moves from "rewrote too much" to "mis-escaped a pointer". The first
  full eval run failed 5 of 152 fixes that way; naming the two escaping traps in the system
  prompt brought it to 152/152, and retry feedback now suggests the correctly escaped pointer
  (`core/pointer_hints.py`).
