# govagent — MVP Specification

> Place this file at `docs/SPEC.md`. `CLAUDE.md` references it.

## 1. Problem & pitch
API governance guidelines exist in most large organizations, but enforcing them is manual: linters flag violations, then humans fix hundreds of them by hand, inconsistently.
**govagent** closes the loop: it detects violations deterministically, has an agent propose minimal, verified fixes, separates safe fixes from breaking or human-judgment ones, and opens a reviewable PR.

What makes it credible (and what the README must show):
- deterministic detection + verification → measurable quality;
- minimal JSON Patch diffs, formatting preserved;
- breaking changes never auto-applied;
- published evals (fix rate, regression rate, cost per analysis);
- cost and blast-radius controls in production.

## 2. MVP scope
**In**: OpenAPI 3.0/3.1 single-file YAML/JSON · custom Spectral ruleset (§5) · LangGraph fix loop · CLI · MCP server (streamable HTTP, API-key auth) · GitHub PR creation on allowlisted repos · eval harness · AWS deployment (ECS Fargate + Bedrock) · structured logs + optional Langfuse tracing.

**Out (README roadmap)**: web UI · multi-tenant · GitHub App auth · multi-file `$ref` · LLM-detected semantic rules · breaking-change detection between versions (oasdiff) · AsyncAPI · RAG over company guidelines · GitHub Action wrapper.

## 3. Domain model (`src/govagent/domain/models.py`)
```python
class Severity(StrEnum):
    ERROR = "error"; WARN = "warn"; INFO = "info"; HINT = "hint"

class FixClass(StrEnum):
    AUTO = "auto"                       # agent may fix
    NEEDS_HUMAN = "needs_human_input"   # requires business knowledge; no LLM call

class Violation(BaseModel, frozen=True):
    rule_id: str
    severity: Severity
    pointer: str            # RFC 6901 JSON pointer
    message: str
    @property
    def fingerprint(self) -> str: return f"{self.rule_id}:{self.pointer}"

class RuleMeta(BaseModel, frozen=True):
    rule_id: str
    fix_class: FixClass
    breaking: bool
    scope: str               # template resolving the group scope, e.g. "operation", "paths", "schema"
    extra_write_scopes: list[str] = []   # e.g. ["/components/schemas", "/components/responses"]
    guidance: str            # short fixing guidance injected in the prompt

class PatchOp(BaseModel, frozen=True):
    op: Literal["add", "remove", "replace", "move"]
    path: str
    value: Any | None = None
    from_: str | None = Field(default=None, alias="from")

class ViolationGroup(BaseModel, frozen=True):
    id: str                  # stable hash of rule_id + scope_pointer
    rule_id: str
    scope_pointer: str
    violations: list[Violation]

class FixProposal(BaseModel, frozen=True):
    id: str
    group_id: str
    ops: list[PatchOp]
    rationale: str           # 1-3 sentences, used in the PR body
    breaking: bool           # copied from RuleMeta, never from the LLM
    attempt: int

class FixStatus(StrEnum):
    RESOLVED = "resolved"
    FAILED = "failed"                    # attempts exhausted
    REJECTED_SCOPE = "rejected_scope"    # ops outside allowed scopes (counts as a failed attempt)
    NEEDS_HUMAN = "needs_human_input"
    BUDGET_EXCEEDED = "budget_exceeded"

class FixOutcome(BaseModel, frozen=True):
    group_id: str
    status: FixStatus
    proposal: FixProposal | None
    attempts: int
    introduced: list[Violation] = []     # new violations caused by the last attempt

class Usage(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

class AnalysisReport(BaseModel, frozen=True):
    analysis_id: str
    spec_sha256: str
    initial_violations: list[Violation]
    outcomes: list[FixOutcome]
    final_violations: list[Violation]   # after applying all RESOLVED proposals
    usage: Usage
    duration_ms: int
```

Ports (`domain/ports.py`, `typing.Protocol`):
- `Linter.lint(spec_text: str) -> list[Violation]`
- `FixModel.propose(request: FixRequest) -> tuple[LLMFixOutput, Usage]`
- `GitHost.get_file(repo, path, ref) -> (text, sha)` · `create_branch` · `update_file` · `open_pr -> url`

## 4. Deterministic core (`src/govagent/core/`)
- **spec_io.py**: ruamel round-trip load/dump; `resolve(doc, pointer)`; reject external `$ref`; validate with `openapi-spec-validator`. Indentation is detected from the source so an unmodified YAML spec dumps byte-identical (known limitation: explicit `null` / `~` become empty values). JSON specs are written back with `json.dumps` and the detected indentation.
- **fragments.py**: `render_fragment(doc, pointer, max_depth=4, max_chars=8000)`. Renders the subtree as YAML, collapsing deeper nodes to `{…}` / `[…]`. For `paths` scope, renders keys only.
- **patching.py**: in-house applier for `add/remove/replace/move` directly on ruamel nodes, so comments and ordering are preserved. Raises `PatchError` on invalid paths. No `copy`/`test`.
- **scope_guard.py**: an op is allowed only if `path` (and `from` for move) starts with the group's `scope_pointer` or one of the rule's `extra_write_scopes`.
- **grouping.py**: group violations by `(rule_id, scope_pointer)`. Skip LLM entirely for `NEEDS_HUMAN` rules.
- **verification.py**: given before/after violation sets and the group's target fingerprints:
  - `resolved` = all targets gone AND no new `error`/`warn` introduced;
  - `introduced` = after − before (by fingerprint).
  - Pre-existing violations under a `move` source are relocated before comparing, so a rename does not look like it introduced them. Known limitation: fingerprints can still shift after array insertions/removals; documented, acceptable for MVP.

Fixes are applied **sequentially** on a working copy: each resolved proposal becomes the base for the next group. Because a fix can move pointers (a path rename moves every operation under it), the group queue is recomputed from the re-lint after each resolved fix; groups that already have an outcome are skipped. The report keeps proposals in application order.

Beyond re-linting, a fix is rejected if the patched document is no longer valid OpenAPI (including unresolvable `$ref`s) or if it creates `required` entries that name missing properties (`core/integrity.py`): neither is visible to the ruleset.

## 5. Ruleset (`rulesets/`)
`governance.spectral.yaml`: Spectral rules. `rules_meta.yaml`: `RuleMeta` per rule. Content inspired by public guidelines only.

| rule_id | Check | fix_class | breaking | scope (+ extra) |
|---|---|---|---|---|
| gov-info-contact | `info.contact.email` present | needs_human | no | /info |
| gov-servers-https | server URLs use `https://` | auto | no | /servers |
| gov-operation-id | every operation has `operationId` | auto | no | operation |
| gov-operation-id-camel | `operationId` is camelCase | auto | **yes** (codegen) | operation |
| gov-operation-summary | `summary` present | auto | no | operation |
| gov-operation-description | `description` present | needs_human | no | operation |
| gov-operation-tags | at least one tag | auto | no | operation (+ /tags) |
| gov-path-kebab | static path segments kebab-case | auto | **yes** | /paths |
| gov-path-no-trailing-slash | no trailing slash | auto | **yes** | /paths |
| gov-error-response | each operation defines ≥1 4xx response | auto | no | operation (+ /components/schemas, /components/responses) |
| gov-problem-json | 4xx/5xx use `application/problem+json` (RFC 9457) | auto | **yes** | operation (+ /components) |
| gov-property-camel | schema property names camelCase | auto | **yes** | schema (renames must update `required`, examples) |
| gov-security-defined | global or per-operation `security` | needs_human | **yes** | / |

Acceptance: each rule has one passing and one failing fixture under `tests/fixtures/rules/`.

## 6. Agent graph (`src/govagent/agent/`)
State (Pydantic, serializable): `spec_text`, `violations`, `groups` (queue), `current_group`, `attempt`, `last_feedback`, `outcomes`, `usage`, `budget`.

```
lint ──► group ──► next_group ─┬─(queue empty)──────────────► finalize ──► END
                               ├─(needs_human)──► record ──► next_group
                               └─(auto)──► propose ──► guard ──► apply_verify
                                              ▲          │ rejected     │
                                              │          ▼              ├─ resolved ─► record ─► next_group
                                              └── retry (attempt < max, feedback) ◄─┤
                                                                        └─ failed (attempts exhausted) ─► record
```
- **propose**: LLM call with structured output `LLMFixOutput { ops: list[PatchOp], rationale: str, needs_human_input: bool }`. Input: rule id + guidance, violations (pointer + message), scope pointer, allowed write scopes, rendered fragment, and on retry the feedback (violations still present / introduced, or patch/scope error).
- **guard**: scope check → `REJECTED_SCOPE` counts as a failed attempt with feedback.
- **apply_verify**: apply on a copy, re-lint the whole spec, verify (§4).
- **budgets** (config): `max_attempts_per_group=3`, `max_llm_calls_per_run=40`, `max_cost_usd_per_run=0.50`. Checked before every model call (the cost of a call is only known afterwards, so the last call can overshoot the cost cap by at most one call). When exceeded, remaining auto groups get `BUDGET_EXCEEDED`; `needs_human_input` groups are still reported as such.
- **cost**: tokens × prices from config (`GOVAGENT_PRICE_INPUT_PER_MTOK`, `..._OUTPUT_...`), to be filled in from the AWS pricing page for the chosen model.

Prompt rules (system prompt, `prompts.py`): minimal change; only `add/remove/replace/move`; JSON pointers from document root; never touch outside allowed scopes; preserve semantics; when a fix requires business knowledge, return `needs_human_input=true` with no ops; when renaming, update every reference inside scope (`required`, examples, `$ref`s).

## 7. Approval & PR (outside the graph)
`build_pr(report, approved_proposal_ids, original_spec) -> PatchedSpec`:
1. Re-apply approved proposals **in original order** on the original spec.
2. Re-lint. If an approved proposal no longer applies (it depended on an unapproved one), fail with an explicit error listing the dependency.
3. Breaking proposals require explicit approval; `--yes-non-breaking` in the CLI approves only non-breaking ones.

GitHub (`adapters/github.py`, httpx + REST): get file + sha → create branch `govagent/<analysis_id[:8]>` from base → update file → open PR.
PR body contains: summary table (resolved / failed / needs human / breaking), one section per applied fix (rule, rationale, ops), remaining violations, usage/cost.
**Repo allowlist** (`GOVAGENT_REPO_ALLOWLIST`): any other repo is refused.

## 8. Interfaces
**CLI (Typer)**
```
govagent lint SPEC [--format table|json]
govagent fix SPEC [--out FILE] [--report report.json] [--dry-run] [--interactive | --yes-non-breaking]
govagent pr --repo OWNER/NAME --path openapi.yaml --base main [--interactive | --yes-non-breaking]
```
Interactive mode shows a colored diff per proposal (with a BREAKING badge) and asks y/n.

**MCP server (FastMCP, streamable HTTP, stateless)**, mounted in a Starlette app with `/healthz`:
- `lint_spec(spec_content?: str, repo?: str, path?: str, ref?: str) -> violations`
- `propose_fixes(spec_content? | repo/path/ref) -> {analysis_id, summary, proposals[] with unified diff preview + breaking flag, needs_human[]}`
- `open_pull_request(analysis_id, approved_fix_ids[], repo, path, base) -> {pr_url}`

Analysis store: in-memory with 1h TTL (single task). Documented limitation; V2 = DynamoDB.
Auth: `Authorization: Bearer <GOVAGENT_MCP_API_KEY>` middleware on everything except `/healthz`. Basic per-key rate limit.

## 9. Milestones (dependency order) & acceptance criteria
**M0 — Foundations**
uv project, ruff/pyright/pytest, pre-commit, CI (lint + types + unit tests), Dockerfile (python:3.12-slim + Node + pinned Spectral), `Settings`, `.env.example`, ruleset files with 2–3 rules.
✅ `docker build` succeeds and `docker run govagent spectral --version` works; CI green.

**M1 — Walking skeleton**
One rule (`gov-operation-summary`), one fixture spec, CLI `fix`: lint → one Bedrock call → patch → re-lint → print resolved. Quick and dirty is fine; stays behind a flag or gets replaced in M2–M3.
✅ The real Bedrock call resolves the violation on the fixture.

**M2 — Deterministic core**
§4 complete: spec_io, fragments, patching on ruamel, scope guard, grouping, verification. Spectral adapter (pointer conversion, exit-code handling).
✅ Round-trip test: load + dump an unmodified spec with comments → byte-identical. Patch tests for each op incl. on commented YAML. All 13 rule fixtures pass/fail as expected.

**M3 — Agent graph**
§6 complete with fake model in tests; Bedrock adapter with structured output; budgets; usage/cost tracking.
✅ Scripted fake-model tests cover: resolved first try, resolved on retry, scope rejection, failed after max attempts, budget exceeded, needs_human skip. Invariant test: breaking never auto-approved.

**M4 — Eval harness** (start dataset in parallel with M3)
- `evals/seeds/`: 5–8 lint-clean specs (written by hand or permissively licensed; record the source + license in `evals/seeds/SOURCES.md`).
- `evals/mutators.py`: one deterministic mutator per auto rule (seeded RNG) injecting a violation at an eligible location.
- `build_dataset.py`: single-violation cases (seed × mutator) + multi-violation cases (3–5 mutators combined). Stores expected fingerprints.
- `run.py --model ID --subset smoke|full`: runs the graph per case, writes `evals/results/<date>_<model>.json` + `.md`.
- Metrics: dataset sanity (linter detects 100% of injected violations), **fix rate** (auto-fixable resolved / injected), **regression rate** (cases with introduced violations), **valid spec rate** (openapi-spec-validator), mean attempts, **cost per case**, p50/p95 latency. Breakdown per rule.
✅ Full run on two models (one small, one larger), results table committed.

**M5 — Interfaces**
CLI complete (§8), MCP server with auth + analysis store.
✅ Claude Desktop (or MCP Inspector) connects to the local server and runs lint → propose → open PR end to end.

**M6 — GitHub integration**
§7 complete, allowlist, PR body template.
✅ A real PR opened on a public demo repo (`govagent-demo` with a deliberately messy spec).

**M7 — AWS deployment** (Terraform, `infra/terraform/`)
- S3 backend with native lockfile.
- ECR; ECS cluster + Fargate service (1 task, 0.5 vCPU / 1 GB); ALB + HTTPS (ACM cert, subdomain on the personal domain, DNS at Cloudflare).
- Secrets Manager: GitHub token, MCP API key. Task role: `bedrock:InvokeModel` on the specific model/inference-profile ARNs only + `secretsmanager:GetSecretValue` on those two secrets.
- CloudWatch logs (14-day retention).
- **AWS Budgets alarm created in the very first apply.**
- Region chosen for Bedrock model availability (check before choosing).
- GitHub Actions deploy via **OIDC role** (no static AWS keys): build → push ECR → update service, manual trigger.
- Cost tip: `desired_count = 0` when not demoing.
✅ Remote MCP endpoint reachable from Claude Desktop with auth; unauthenticated request → 401; budget alarm exists.

**M8 — Observability & packaging**
structlog JSON with `run_id`; optional Langfuse callback via env. Eval workflow on `workflow_dispatch` + push to main (smoke subset, OIDC → Bedrock).
README (English): problem → demo GIF (Claude Desktop fixes a spec and opens a PR) → architecture diagram → eval results table → design decisions (link ADRs) → limitations → roadmap → quickstart.
ADRs: 001 deterministic detection / LLM fixes only · 002 JSON Patch over full rewrite · 003 approval outside the graph · 004 deterministic breaking classification · 005 ECS Fargate over Lambda.
✅ Definition of done (§10).

## 10. Definition of done (MVP)
- [ ] Deployed on AWS, reachable over MCP from Claude Desktop, authenticated
- [ ] One real PR opened on the public demo repo
- [ ] Reproducible evals with published numbers for 2 models; smoke subset in CI
- [ ] README with demo GIF + eval table + ADRs
- [ ] Cost per analysis known and capped by config; AWS budget alarm active

## 11. Configuration (`GOVAGENT_` prefix)
`MODEL_ID`, `AWS_REGION`, `RULESET_PATH`, `RULES_META_PATH`, `SPECTRAL_BIN`, `MAX_ATTEMPTS_PER_GROUP`, `MAX_LLM_CALLS_PER_RUN`, `MAX_COST_USD_PER_RUN`, `PRICE_INPUT_PER_MTOK`, `PRICE_OUTPUT_PER_MTOK`, `GITHUB_TOKEN`, `REPO_ALLOWLIST`, `MCP_API_KEY`, `ANALYSIS_TTL_SECONDS`, `LOG_LEVEL`, optional `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`.
