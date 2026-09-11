# Tool authorization

Secure AI Gateway applies least-privilege authorization to function tools both **before model exposure** and **again before execution**.

This document describes the exposure boundary. See `docs/tool-execution-authorization.md` for the independent execution-time boundary.

## Exposure security boundary

A model request can contain function definitions in `tools` and can optionally select a tool through `tool_choice`. Those definitions are capabilities: exposing a powerful function to a model gives the model an opportunity to request that capability.

The gateway therefore authorizes tool exposure against the authenticated client identity before the request reaches rate, model, PII, prompt-injection, usage-budget, or provider execution.

A returned model `tool_call` is still untrusted. Before a downstream executor can act, the gateway now validates the exact returned function/schema/arguments, issues a short-lived execution ticket, and requires a second authorization decision through `/v1/tool-executions/authorize`.

## Configuration

Tool policy is mandatory for valid protected chat requests:

```dotenv
SAG_CLIENT_ALLOWED_TOOLS=local-dev:-
```

A hyphen explicitly grants no tools. This is different from missing configuration; missing or malformed policy fails closed with `503`.

Grant individual functions by name:

```dotenv
SAG_CLIENT_ALLOWED_TOOLS=agent-a:search,agent-a:calculator,agent-b:lookup_ticket
```

When unified security-policy profiles are enabled, `profile.allowed_tools` is the normal source of these grants. A client may expose only functions named in its allowlist. There is no wildcard grant.

## Request behavior

A client with a `calculator` grant may submit:

```json
{
  "model": "some-model",
  "messages": [
    {"role": "user", "content": "Calculate 2+2"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression",
        "parameters": {
          "type": "object",
          "properties": {
            "expression": {"type": "string"}
          },
          "required": ["expression"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

If every declared function is allowed, the request continues and includes:

```text
X-Tool-Authorization-Action: allowed
X-Tool-Requested-Count: 1
```

If any function is not allowed, the entire request is rejected before provider forwarding:

```text
HTTP/1.1 403 Forbidden
X-Tool-Authorization-Action: denied
X-Tool-Requested-Count: 1
```

```json
{"detail":"Requested tool is not allowed."}
```

A named `tool_choice` must also name a tool declared in the same request. This prevents a caller from forcing an undeclared function name even when that name appears in its broader allowlist.

## Audit behavior

Tool exposure authorization emits safe metadata such as validated tool names and counts. It deliberately does not log function arguments, tool-result content, prompt text, or provider response bodies.

Example:

```json
{
  "event": "tool_authorization",
  "outcome": "deny",
  "client_id": "agent-a",
  "reason": "tool_not_allowed",
  "tool_requested_count": 2,
  "tool_requested_names": "calculator,delete_account",
  "tool_denied_names": "delete_account"
}
```

Execution-time audit additionally records safe tool-call/execution IDs and risk labels, but still omits arguments and ticket contents.

## OpenAI-compatible proxying

The chat request/response models support function `tools`, named/string `tool_choice`, assistant `tool_calls`, and tool-result messages. Authorized tool definitions are forwarded by the OpenAI-compatible provider adapter and returned tool calls are normalized back through the gateway response schema.

A returned call that passes execution preparation receives gateway-only `execution_token` and `execution_risk` fields. Those fields are stripped from subsequent assistant-message history before any request is sent back to OpenAI/OpenRouter.

Only function tools are supported. Provider-native built-in tools and MCP tools require their own capability and execution policies rather than being implicitly accepted through this allowlist.

## Design principle

Tool names are permissions, not instructions. Model output is untrusted data. A model asking to call an authorized tool does not itself authorize a real-world action.

The complete path is therefore:

```text
client identity
    -> exposure authorization
    -> model tool selection
    -> returned-call/schema/argument validation
    -> short-lived execution ticket
    -> execution-time re-authorization
    -> downstream side effect
```

See `docs/tool-execution-authorization.md` for ticket binding, replay protection, schema authority, risk classification, and the executor contract.
