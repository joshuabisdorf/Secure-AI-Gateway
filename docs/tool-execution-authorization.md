# Execution-time tool authorization

Secure AI Gateway treats every model-generated function call as untrusted data. Exposure-time authorization controls which functions a client may show to a model; execution-time authorization independently decides whether an exact returned call may proceed immediately before a downstream executor performs a side effect.

The gateway still does not execute external tools itself. This milestone provides the authorization boundary that a real executor must call before acting.

## Security model

The flow is deliberately two-stage:

```text
Authenticated client
      |
      |  tools[]
      v
Exposure authorization
      |
      |  allowed function definitions only
      v
Upstream model
      |
      |  untrusted tool_call
      v
Gateway output validation
      |
      |  short-lived signed execution ticket
      v
Application / executor
      |
      |  POST /v1/tool-executions/authorize
      v
Execution-time authorization
      |
      |  allow exactly once
      v
Actual side effect
```

A model deciding to call a tool is never treated as permission to execute it.

## Authoritative tool registry

Per-client `allowed_tools` continues to answer one question:

> May this authenticated client use this function at all?

The execution registry answers two different questions:

- What exact JSON Schema defines valid arguments for this function?
- What risk class does the function have?

The tracked example is:

```text
config/tool-execution-policies.example.json
```

Version 1 supports three risk labels:

```text
read
write
destructive
```

Example:

```json
{
  "version": 1,
  "tools": {
    "status_check": {
      "risk": "read",
      "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      }
    }
  }
}
```

The registry is non-secret policy. For a real deployment, copy it to the ignored local path if customization is needed:

```bash
cp config/tool-execution-policies.example.json config/tool-execution-policies.json
```

`config/tool-execution-policies.json` is ignored by Git and excluded from the Docker build context.

## Schema substitution defense

The client-supplied function definition is not authoritative at execution time.

When a provider returns a tool call, the gateway verifies that:

1. the function is still in the authenticated client's allowlist;
2. the function was declared in the request that produced the call;
3. the request-declared parameter schema has the same canonical SHA-256 fingerprint as the authoritative execution registry;
4. the model-returned arguments are valid JSON;
5. the arguments satisfy the authoritative Draft 2020-12 JSON Schema.

This prevents a client from taking an allowed function name and supplying a weaker schema to broaden what the executor will accept.

## Execution tickets

After a returned tool call passes those checks, the gateway adds two gateway-only fields to that tool call:

```json
{
  "execution_token": "<short-lived signed capability>",
  "execution_risk": "read"
}
```

The execution token is an HMAC-SHA256-signed capability. It is tamper-evident, not encrypted, and must be treated as a secret bearer credential by the application receiving it.

The signed payload binds:

- authenticated `client_id`;
- authenticated gateway `key_id`;
- source gateway request ID;
- provider tool-call ID;
- exact function name;
- SHA-256 of the exact argument string;
- authoritative schema fingerprint;
- risk classification;
- expiration time;
- unique execution ID.

Raw function arguments are not copied into the ticket payload.

Tickets default to 120 seconds and cannot be configured above 900 seconds.

## Configuration

Runtime configuration:

```dotenv
SAG_TOOL_EXECUTION_POLICY_FILE=config/tool-execution-policies.json
SAG_TOOL_EXECUTION_SIGNING_KEY=<at-least-32-random-bytes>
SAG_TOOL_EXECUTION_TTL_SECONDS=120
SAG_TOOL_EXECUTION_REPLAY_BACKEND=redis
```

Generate a local signing value without sharing it:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store the output only in the ignored `.env` file or an appropriate secret manager.

The signing key is required only when the gateway needs to issue or verify an execution ticket. Ordinary text-only requests do not need it.

## Authorization endpoint

A downstream executor submits the exact gateway-returned tool call immediately before performing the action:

```http
POST /v1/tool-executions/authorize
Authorization: Bearer <gateway-client-key>
Content-Type: application/json
```

```json
{
  "tool_call": {
    "id": "call_123",
    "type": "function",
    "function": {
      "name": "status_check",
      "arguments": "{}"
    },
    "execution_token": "<gateway-issued-ticket>",
    "execution_risk": "read"
  }
}
```

A successful decision returns only safe metadata:

```json
{
  "authorized": true,
  "execution_id": "...",
  "source_request_id": "req_...",
  "tool_name": "status_check",
  "risk": "read"
}
```

and:

```text
X-Tool-Execution-Authorization: allowed
```

The executor may perform the side effect only after receiving this successful decision.

## Complete mediation

The authorization endpoint does not trust the earlier exposure decision. It independently:

- authenticates the current gateway client/key;
- verifies ticket HMAC and expiration;
- binds the exact tool-call ID and name;
- verifies the exact argument digest;
- verifies the signed risk label;
- reloads the current authoritative execution registry;
- validates arguments again;
- reloads the current client tool allowlist;
- denies the action if the tool was revoked after the model response.

This means an authorization decision is based on current policy at the point of execution rather than only on policy that existed when the model first saw the tool.

## Replay protection

Each execution ticket has a unique execution ID and may authorize only once.

Runtime deployments use Redis with an atomic `SET ... NX EX` claim under:

```text
sag:tool_execution:<execution_id>
```

Only one gateway replica can successfully claim the ticket. A repeated authorization request returns `409 Conflict`.

The deterministic test suite uses an in-memory replay store instead of Redis.

## Risk classification

`read`, `write`, and `destructive` are security metadata, not automatic permission escalation.

They are intended to drive future executor policy such as:

- separate service identities for read versus write operations;
- explicit human approval for destructive/high-impact actions;
- stronger audit/alerting for sensitive actions;
- different rate limits or approval workflows by risk.

An executor must still use least-privilege downstream credentials. Gateway authorization does not grant permissions that the downstream system itself would otherwise deny.

## Provider credential boundary

`execution_token` and `execution_risk` are gateway-only metadata. If an application later includes an assistant tool call in chat history, the OpenAI-compatible provider adapter strips those fields before sending the message upstream.

Execution tickets therefore are not intentionally disclosed to OpenAI/OpenRouter as part of conversation history.

## Audit behavior

Safe audit fields include:

- validated tool name;
- tool-call ID;
- gateway execution ID;
- risk class;
- source request ID;
- allow/deny/error reason.

Audit events deliberately omit:

- execution-token contents;
- raw function arguments;
- tool results;
- prompt/message text;
- gateway/provider credentials.

Relevant event categories are:

```text
tool_execution_ticket
tool_execution_authorization
```

## Failure behavior

The boundary fails closed. Examples include:

- tool removed from current client policy;
- unknown execution tool;
- client-declared schema differs from the authoritative schema;
- malformed or schema-invalid arguments;
- altered function name/ID/arguments/risk;
- altered or expired ticket;
- wrong authenticated client/key;
- changed authoritative execution policy;
- replayed ticket;
- unavailable Redis replay protection.

No failed authorization should be interpreted by an executor as permission to proceed.

## Current limitation

The gateway authorizes function execution but does not yet implement real side-effecting connectors/executors. That is intentional: adding external actions without this authorization boundary would invert the security design.

When real executors are added, they must call `/v1/tool-executions/authorize` immediately before the side effect and must not cache an earlier allow decision.
