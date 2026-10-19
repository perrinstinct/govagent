# ADR 005: Deployment is optional; ECS Fargate if deployed

- Status: accepted
- Recorded: 2026-10-19 (decided when framing M7)

## Context

The specification planned a hosted MCP endpoint: ECS Fargate behind an Application Load
Balancer with HTTPS, in eu-west-3 next to Bedrock. Pricing it from the AWS Pricing API
(eu-west-3, October 2026) changed the picture:

| Item | $/month if always on |
|---|---|
| ALB (hour + minimum capacity) | ~20 |
| ALB public IPv4 addresses (2 AZs) | 7.30 |
| Fargate task, 0.5 vCPU / 1 GB | 21.60 |
| Task public IPv4 | 3.65 |
| Secrets, registry, logs, state | ~1.30 |
| **Total** | **~54** |

Scaling the service to zero tasks only removes Fargate: the load balancer and its addresses
keep billing (~28 $/month). The project's purpose is to show the agent itself (LangGraph,
evals, verification), which runs the same locally.

## Decision

The MVP ships a container image (`Dockerfile`, Spectral included) and an MCP server that any MCP
client can use locally (`govagent serve`). Hosting it is optional and on the roadmap.

If it is deployed, use two Terraform stacks: `base`, always on (~1.30 $/month: state, ECR,
secrets, IAM roles, GitHub OIDC role, certificate, budget alarm), and `service` (load balancer,
ECS service, DNS record), applied for a demo and destroyed after (~0.07 $/hour).

Fargate rather than Lambda for the service:

- an analysis can run well beyond the ~30 s integration timeout of API Gateway (multi-violation
  eval cases already take 8 to 18 s on small specs);
- one container holds both runtimes the server needs (Python and the Node-based Spectral CLI);
- the in-memory analysis store between `propose_fixes` and `apply_fixes` assumes one long-lived
  process.

## Consequences

- No standing cost and no public endpoint spending Bedrock money while nobody uses it.
- Showing a hosted demo takes one `terraform apply` once the stacks exist; until then demos run
  against a local server.
- Several tasks would need a shared analysis store (DynamoDB) instead of the in-memory one.
