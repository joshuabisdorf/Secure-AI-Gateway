# ADR 0001: Separate tool exposure from execution authorization

- Status: Accepted
- Date: 2026-09-12

## Context

LLM applications commonly expose tool/function schemas to a model and then treat a returned tool call as permission to invoke the corresponding side effect. That collapses two different security decisions: whether a model may know about/request a capability, and whether an authenticated executor may perform the capability at a later point in time.

Model output is attacker-influenceable and must not be an authorization source. Client policy can also change between model generation and execution.

## Decision

Secure AI Gateway separates tool exposure authorization from execution-time authorization.

At request time, the authenticated client's allowlist controls which tools may be exposed to the model. At response time, every model-generated tool call is treated as untrusted and must match the request declaration, current client grant, authoritative tool registry, exact authoritative JSON Schema fingerprint, and argument schema.

For valid calls, the gateway issues a short-lived HMAC-SHA256 execution ticket bound to:

- authenticated client ID;
- gateway key ID;
- source request ID;
- tool-call ID;
- tool name;
- exact argument-string SHA-256;
- authoritative schema SHA-256;
- risk class;
- expiration time;
- unique execution ID.

A downstream executor must independently call `/v1/tool-executions/authorize`. The gateway re-authenticates the caller, verifies the ticket, re-resolves current policy/schema/risk state, validates the exact arguments again, and atomically claims the execution ID once through the replay store.

The gateway does not itself execute external side-effecting tools.

## Consequences

Benefits:

- model output alone cannot authorize a side effect;
- tool permission revocation takes effect before execution;
- argument/schema tampering invalidates authorization;
- tickets are identity-bound and short-lived;
- replay is rejected across gateway replicas when Redis/Valkey is used;
- risk classification comes from trusted policy rather than model output.

Costs:

- downstream executors require an additional authorization round trip;
- execution authorization depends on signing-key, policy, identity, and replay-store availability;
- the system intentionally fails closed when those dependencies are unavailable.

## Rejected alternatives

### Execute model tool calls directly in the gateway

Rejected because it makes untrusted model output too close to a side-effect boundary and would require every executor-specific security control to live inside the gateway.

### Treat request-time tool exposure as sufficient authorization

Rejected because policy, identity, schema, and risk state can change after the model response is generated.

### Put raw tool arguments in signed/audit metadata

Rejected to reduce sensitive-data propagation. The ticket binds the exact argument bytes by digest instead.
