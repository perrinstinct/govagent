# CLAUDE.md — govagent (API Governance Agent)

## What this project is
An agent that enforces API design governance on OpenAPI 3.x specs:
lint (deterministic) → agent proposes JSON Patch fixes → apply + re-lint to verify → retry or give up → report → human approval → GitHub PR.
Exposed as a CLI and as an MCP server (streamable HTTP), deployed on AWS (ECS Fargate + Bedrock).

Public portfolio project. Code quality, tests, evals and README matter as much as features.
Full spec: `docs/SPEC.md`. Read the relevant section before starting a milestone.

## Non-negotiable design rules
1. **The LLM never detects violations — it only fixes them.** Detection and verification are done by Spectral + our ruleset. Every fix is verified by re-linting.
2. **The LLM never rewrites the whole spec.** It returns JSON Patch ops (`add`, `remove`, `replace`, `move` only) scoped to allowed pointers. Out-of-scope ops are rejected deterministically before application.
3. **Fix class and breaking-ness are deterministic** (rule metadata in `rulesets/rules_meta.yaml`). The LLM may downgrade a fix to `needs_human_input`, never upgrade it. Breaking fixes are never auto-approved.
4. **YAML formatting and comments must survive.** Always load/dump with `ruamel.yaml` round-trip mode. Never `yaml.safe_load` / `json.dumps` a YAML spec we write back. JSON specs (no comments) are the one exception: `core/spec_io.py` writes them back with `json.dumps` and the detected indentation.
5. **Human approval lives outside the agent graph.** The graph ends at the report; PR creation is a separate deterministic step taking approved fix IDs.
6. **Budgets are enforced in code**: max attempts per group, max LLM calls per run, max cost per run.

## Stack
Python 3.12 · uv · Pydantic v2 + pydantic-settings · LangGraph · langchain-aws (Bedrock Converse) · Spectral CLI (Node, subprocess) · ruamel.yaml · official `mcp` Python SDK (FastMCP) · Typer · httpx (GitHub REST) · structlog · pytest · ruff · pyright · Terraform.

## Layout & dependency direction
```
src/govagent/
  domain/      models + ports (Protocols). Imports nothing from the project.
  core/        deterministic logic: spec_io, fragments, patching, verification, grouping. Imports domain only.
  agent/       LangGraph state, nodes, graph, prompts. Imports domain + core + ports.
  adapters/    spectral, bedrock, github — implement ports.
  interfaces/  cli.py, mcp_server.py — composition root, wiring only.
rulesets/      governance.spectral.yaml + rules_meta.yaml
evals/         seeds/, mutators.py, build_dataset.py, run.py, results/
tests/         unit/, integration/, fixtures/
infra/terraform/
docs/          SPEC.md, adr/
```
Never import `adapters` or `interfaces` from `domain`, `core` or `agent`.

## Commands
```bash
uv sync                                   # install
uv run pytest                             # unit tests (default: no Spectral, no Bedrock)
uv run pytest -m integration              # needs Spectral on PATH
uv run pytest -m bedrock                  # real Bedrock calls — only when asked
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run govagent lint path/to/spec.yaml
uv run govagent fix path/to/spec.yaml --dry-run
uv run python -m evals.run --subset smoke
docker build -t govagent .
```
Spectral locally: `npm i -g @stoplight/spectral-cli` (pin the version used in the Dockerfile).

## Conventions
- Type hints everywhere; pyright strict on `src/`.
- Domain models are Pydantic v2, frozen where possible.
- Core is synchronous. The MCP layer is async and offloads core calls with `anyio.to_thread.run_sync`.
- No `print` — use structlog, JSON logs, always bind `run_id`.
- Config only via `govagent.config.Settings` (env prefix `GOVAGENT_`). No hardcoded model IDs, prices, regions or tokens.
- Errors: custom exceptions in `domain/errors.py`; never swallow exceptions silently.

## Testing rules
- Every change to `core/` comes with unit tests.
- Agent tests use a fake chat model returning scripted proposals. Unit tests never hit Bedrock or the network.
- Spectral-dependent tests are marked `@pytest.mark.integration`.
- Invariant test that must always pass: a fix whose rule is `breaking: true` is never auto-approved.

## Working agreement
- Work milestone by milestone (`docs/SPEC.md` §9). Respect each milestone's acceptance criteria before moving on.
- Ask before: changing domain models, adding a dependency, changing the ruleset semantics, or touching infra.
- Record significant design decisions as short ADRs in `docs/adr/NNN-title.md`.
- Never commit secrets. `.env` is gitignored; `.env.example` lists every variable.
- Ruleset content comes from public API guidelines (Zalando, Adidas, RFC 9457). No rule or content from any former employer.

## Known gotchas
- Spectral exits non-zero when it finds violations: parse stdout JSON, don't treat the exit code as a crash. Real failures are empty or invalid stdout.
- Spectral returns paths as segment arrays → convert to RFC 6901 JSON pointers (escape `~` → `~0`, `/` → `~1`).
- MVP supports single-file specs only (no external `$ref`). Reject multi-file specs with a clear error.
- Violation fingerprints (`rule_id:pointer`) can shift after array removals or key moves. Acceptable for the MVP; documented in SPEC §4.
- ruamel cannot preserve explicit `null` / `~`: they are written back as empty values (`key:`). Everything else in an unmodified YAML spec round-trips byte-identical.
