# Eval results: `eu.anthropic.claude-haiku-4-5-20251001-v1:0` (full)

| Metric | Value |
|---|---|
| Cases | 84 (0 errors, 0 skipped) |
| Dataset sanity (injected violations detected) | 100% |
| **Fix rate** (fixed / injected) | **96.7%** (147/152) |
| **Regression rate** (cases with new violations) | **0.0%** |
| Valid spec rate | 100.0% |
| Mean attempts per fix | 1.07 |
| LLM calls | 163 (221684 in / 27804 out tokens) |
| **Cost per case** | **$0.0047** (total $0.40) |
| Latency p50 / p95 | 2.6 s / 12.1 s |

| Rule | Injected | Fixed | Fix rate | Mean attempts |
|---|---|---|---|---|
| `gov-error-response` | 15 | 15 | 100% | 1.07 |
| `gov-operation-id` | 19 | 19 | 100% | 1.00 |
| `gov-operation-id-camel` | 14 | 14 | 100% | 1.00 |
| `gov-operation-summary` | 15 | 15 | 100% | 1.00 |
| `gov-operation-tags` | 13 | 12 | 92% | 1.15 |
| `gov-path-kebab` | 12 | 12 | 100% | 1.00 |
| `gov-path-no-trailing-slash` | 16 | 15 | 94% | 1.12 |
| `gov-problem-json` | 15 | 12 | 80% | 1.40 |
| `gov-property-camel` | 16 | 16 | 100% | 1.00 |
| `gov-servers-https` | 17 | 17 | 100% | 1.00 |
