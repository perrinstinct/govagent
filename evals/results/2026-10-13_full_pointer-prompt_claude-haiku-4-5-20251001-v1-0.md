# Eval results: `eu.anthropic.claude-haiku-4-5-20251001-v1:0` (full)

| Metric | Value |
|---|---|
| Cases | 84 (0 errors, 0 skipped) |
| Dataset sanity (injected violations detected) | 100% |
| **Fix rate** (fixed / injected) | **100.0%** (152/152) |
| **Regression rate** (cases with new violations) | **0.0%** |
| Valid spec rate | 100.0% |
| Mean attempts per fix | 1.03 |
| LLM calls | 157 (230304 in / 26555 out tokens) |
| **Cost per case** | **$0.0048** (total $0.40) |
| Latency p50 / p95 | 2.4 s / 11.1 s |

| Rule | Injected | Fixed | Fix rate | Mean attempts |
|---|---|---|---|---|
| `gov-error-response` | 15 | 15 | 100% | 1.33 |
| `gov-operation-id` | 19 | 19 | 100% | 1.00 |
| `gov-operation-id-camel` | 14 | 14 | 100% | 1.00 |
| `gov-operation-summary` | 15 | 15 | 100% | 1.00 |
| `gov-operation-tags` | 13 | 13 | 100% | 1.00 |
| `gov-path-kebab` | 12 | 12 | 100% | 1.00 |
| `gov-path-no-trailing-slash` | 16 | 16 | 100% | 1.00 |
| `gov-problem-json` | 15 | 15 | 100% | 1.00 |
| `gov-property-camel` | 16 | 16 | 100% | 1.00 |
| `gov-servers-https` | 17 | 17 | 100% | 1.00 |
