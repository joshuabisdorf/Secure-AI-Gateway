# Unified security policy profiles

Secure AI Gateway can load all per-client security controls from one versioned, non-secret JSON registry.

The deployment-wide model ceiling and infrastructure/provider credentials remain outside this file. A client profile can narrow model access, but it cannot widen `SAG_ALLOWED_MODELS`.

## Enable the registry

Create a local policy file from the tracked example:

```bash
cp config/security-policies.example.json config/security-policies.json
```

The local file is ignored by Git so environment-specific client assignments do not need to be committed.

Configure the gateway:

```dotenv
SAG_SECURITY_POLICY_FILE=config/security-policies.json
```

Validate before starting the gateway:

```bash
set -a
source .env
set +a
python -m app.policy_cli validate
```

A valid registry prints only non-secret metadata:

```text
VALID security_policy version=1 profiles=2 clients=1
```

## Version 1 schema

```json
{
  "version": 1,
  "profiles": {
    "local-default": {
      "allowed_models": ["openrouter/free"],
      "requests_per_minute": 10,
      "daily_budget": {
        "tokens": 50000,
        "cost_usd": "1.00"
      },
      "pii_action": "redact",
      "prompt_injection_action": "audit",
      "allowed_tools": []
    }
  },
  "clients": {
    "local-dev": "local-default"
  }
}
```

Each client is assigned to exactly one named profile. Multiple clients may share the same profile.

### Profile fields

`allowed_models` is the client's model grant. It must contain at least one exact model name, and every request must still pass the separate deployment-wide `SAG_ALLOWED_MODELS` ceiling.

`requests_per_minute` is the client's positive fixed-window Redis rate limit.

`daily_budget.tokens` is a positive integer token ceiling or `null` to disable token enforcement. `daily_budget.cost_usd` is a positive decimal string or `null` to disable cost enforcement. Both dimensions cannot be disabled simultaneously. Cost is represented as a JSON string so decimal values are not converted through binary floating-point.

`pii_action` is `redact` or `deny`.

`prompt_injection_action` is `audit`, `deny`, or `off`.

`allowed_tools` is the list of function names the client may expose to a model. An empty list means no tools are authorized.

## Startup behavior

When `SAG_SECURITY_POLICY_FILE` is absent, the gateway temporarily retains compatibility with the previous per-control environment variables for migration and deterministic tests.

When `SAG_SECURITY_POLICY_FILE` is present, it becomes authoritative. At process startup the gateway:

1. removes legacy per-client policy inputs from its own process environment;
2. reads and strictly validates the JSON registry;
3. resolves each client assignment to a named profile;
4. compiles the validated profile values into the established enforcement modules.

If the enabled file is missing, unreadable, oversized, malformed, references an unknown profile, contains an unknown field, or otherwise fails validation, the gateway does not fall back to stale legacy grants. Protected requests fail closed.

Policy is loaded at process startup. Restart the gateway after changing the policy file.

## Strict validation

Version 1 intentionally rejects unknown fields and duplicate JSON keys. This prevents misspelled controls from being silently ignored.

The policy file contains authorization and quota configuration, not credentials. Do not put provider API keys, gateway raw API keys, database passwords, Redis passwords, or other secrets in it.

## Legacy migration

Once the unified registry is enabled and validated, remove these settings from the local `.env`:

```text
SAG_CLIENT_ALLOWED_MODELS
SAG_CLIENT_RATE_LIMITS
SAG_CLIENT_DAILY_BUDGETS
SAG_CLIENT_PII_POLICIES
SAG_CLIENT_PROMPT_INJECTION_POLICIES
SAG_CLIENT_ALLOWED_TOOLS
```

Keep deployment/infrastructure settings such as:

```text
SAG_ALLOWED_MODELS
SAG_PROVIDER
SAG_CLIENT_REGISTRY_BACKEND
SAG_USAGE_LEDGER_BACKEND
SAG_RATE_LIMIT_BACKEND
DATABASE_URL
REDIS_URL
OPENROUTER_API_KEY
OPENAI_API_KEY
```

The legacy per-control formats remain only as an internal migration/test compatibility layer. New runtime configuration should use the unified registry.

## Security boundary

A named profile is configuration, not an authentication mechanism. The PostgreSQL-backed gateway API key still establishes client identity. Only after authentication can the gateway select the profile assigned to that `client_id` and apply its restrictions.

Likewise, the policy file never grants authority beyond independent hard boundaries. In particular, profile model grants remain subordinate to `SAG_ALLOWED_MODELS`, and model-generated tool calls remain untrusted until an execution layer independently authorizes any real side effect.
