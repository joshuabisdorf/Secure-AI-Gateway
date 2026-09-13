# Architecture

This page is the reviewer-oriented map of Secure AI Gateway. It complements the implementation-focused documents in `docs/` and the security invariants in `SECURITY.md`.

## System context

```mermaid
flowchart LR
    C[Application / client] -->|sag API key| G[Secure AI Gateway]
    G -->|credential state, usage| P[(PostgreSQL)]
    G -->|rate state, replay claims| R[(Redis / Valkey)]
    G -->|metrics| M[Prometheus]
    G -->|OTLP traces| O[OpenTelemetry Collector]
    G -->|provider request| L[LLM provider / mock]
    L -->|untrusted response + tool proposals| G
    G -->|tool call + signed execution ticket| E[Downstream executor]
    E -->|authorization request| G
```

The gateway is the policy enforcement boundary. Model output is data, not authority. The gateway does not execute side-effecting external tools; it authorizes a downstream executor after re-authentication and execution-time policy checks.

## Request path

```mermaid
flowchart TD
    A[Receive OpenAI-compatible request] --> B[Authenticate client]
    B --> C[Apply distributed rate limit]
    C --> D[Authorize requested model]
    D --> E[Inspect / redact PII]
    E --> F[Inspect prompt injection]
    F --> G[Check persistent usage budget]
    G --> H[Call configured provider]
    H --> I[Record persistent usage]
    I --> J[Validate returned tool calls]
    J --> K[Issue scoped execution ticket when applicable]
    K --> L[Return OpenAI-compatible response]

    B -->|invalid / revoked| X[Controlled denial]
    C -->|limit exceeded| X
    D -->|model denied| X
    E -->|deny policy| X
    F -->|deny policy| X
    G -->|budget exceeded| X
```

Security-critical shared-state dependencies are intentionally fail-closed. Redis loss prevents distributed authorization state from being trusted; PostgreSQL loss prevents durable client/usage state from being trusted. Telemetry export is not an authorization dependency.

## Execution authorization

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Gateway
    participant M as Model provider
    participant E as Executor
    participant R as Redis / Valkey

    C->>G: authenticated chat + allowed tool schema
    G->>M: policy-filtered provider request
    M-->>G: untrusted tool call proposal
    G->>G: validate current grant + schema + arguments
    G-->>C: tool call + short-lived signed ticket
    C->>E: proposed tool call
    E->>G: POST /v1/tool-executions/authorize
    G->>G: re-authenticate + verify ticket + re-check policy
    G->>R: atomic one-time replay claim
    R-->>G: first claim accepted
    G-->>E: allowed
    E->>G: same authorization request again
    G->>R: atomic replay claim
    R-->>G: already consumed
    G-->>E: denied / conflict
```

The execution ticket binds the proposal to the client, tool, authoritative schema/arguments, risk class, expiry, and current policy state. See `docs/tool-execution-authorization.md` and `docs/adr/0001-execution-time-tool-authorization.md` for the detailed design.

## Trust boundaries

| Boundary | Attacker-controlled input | Primary controls |
| --- | --- | --- |
| Client → gateway | HTTP body, headers, model/tool selection | bounded body, authentication, model policy, schema validation, rate limits |
| Prompt → provider | user/system text | PII controls, injection controls, policy-filtered request |
| Provider → gateway | model text, token usage, tool proposals | response normalization, tool validation, output treated as untrusted |
| Executor → gateway | execution ticket + proposed tool call | re-authentication, signature/expiry binding, current-policy recheck, replay claim |
| Gateway → shared state | identity, usage, rate/replay state | PostgreSQL durability, Redis atomic state, fail-closed dependency handling |
| Gateway → telemetry | bounded metadata | prompts, credentials, raw PII, tool arguments/results, and tickets excluded |

## Deployment views

The zero-cost verified path uses Docker Compose locally and a two-replica kind deployment for distributed-runtime verification. Kubernetes applies non-root execution, read-only root filesystems, dropped Linux capabilities, `RuntimeDefault` seccomp, resource bounds, probes, rolling-update constraints, and a PodDisruptionBudget.

The AWS Terraform tree is a reference architecture rather than a required runtime path. It is deliberately separated from the no-cost verification path and guarded against accidental billable apply operations.
