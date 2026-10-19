# ADR 001: Deterministic detection, the LLM only fixes

- Status: accepted
- Recorded: 2026-10-19 (decided in M1, applied from M0)

## Context

API governance tools need to answer two questions: *what is wrong* and *how to fix it*. An LLM
can do both from a prompt, but its detection is not reproducible (it misses violations, invents
others, and answers differently on the same spec), which rules it out as a CI gate. A team must
be able to trust "zero violations" and to know that a fix really fixed something.

## Decision

Detection and verification are deterministic: Spectral with a versioned ruleset
(`rulesets/governance.spectral.yaml`). The LLM is only asked to fix a violation that the linter
found, and every fix is accepted only after the spec is re-linted (plus OpenAPI validation and
integrity checks, see `core/verification.py` and `core/integrity.py`).

## Alternatives considered

- **LLM reviews the spec and fixes it in one pass**: simplest, but neither the findings nor the
  result can be verified.
- **LLM detects, linter double-checks**: still pays for detection with tokens and still needs the
  linter for trust; no benefit over linting first.

## Consequences

- Results are reproducible and measurable: the eval dataset injects violations into lint-clean
  specs and the linter detects 100% of them before the agent runs (`evals/`).
- The agent can only fix what the ruleset can express. Semantic rules ("is this description
  useful?") are out of scope and listed on the roadmap.
- The model never needs the whole spec: it gets the violation, the rule's guidance and a bounded
  fragment, which keeps prompts small (~1,200 input tokens per call in the evals).
