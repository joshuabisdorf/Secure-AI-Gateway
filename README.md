# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, distributed request throttling, usage budgets, sensitive-data controls, prompt-injection controls, tool exposure policy, provider routing, audit logging, and request correlation before requests reach an upstream model.

## Current capabilities

Implemented:

- FastAPI `/health` and OpenAI-style `/v1/chat/completions`
- deterministic non-network mock provider
- OpenAI and OpenRouter upstream providers
- structured high-entropy gateway API keys with SHA-256 verification
- PostgreSQL-backed persistent client/key registry
- database-backed API-key revocation and atomic rotation
- deployment-wide model ceiling plus per-client model grants
- Redis-backed per-client requests-per-minute rate limiting
- per-client UTC-day token/cost budgets with PostgreSQL persistence
- structured PII `redact` and `deny` policies
- deterministic prompt-injection `audit`, `deny`, and `off` policies
- synthetic system-prompt leakage evaluation with disposable canaries
- least-privilege function-tool exposure authorization
- OpenAI-compatible function `tools`, `tool_choice`, tool-result messages, and assistant `tool_calls`
- versioned named security-policy profiles that consolidate per-client controls
- structured one-line JSON audit events with client attribution and request correlation
- sanitized provider failures
- deterministic `pytest` suite that does not call real providers, PostgreSQL, or Redis

The next evaluation milestone is a versioned adversarial dataset with measurable prompt-injection detection and false-positive metrics.

## Request path

```text
Client
  |
  | sag_<key_id>_<secret>
  v
Secure AI Gateway
  |
  +-- PostgreSQL client/key lookup
  +-- constant-time key-hash verification
  +-- named client security profile
  +-- function-tool exposure authorization
  +-- Redis per-client rate limit
  +-- deployment-wide + client model authorization
  +-- PII inspection / redaction or denial
  +-- prompt-injection inspection / audit or denial
  +-- PostgreSQL daily token/cost accounting
  +-- structured audit logging
  +-- provider routing
  |
  v
OpenRouter / OpenAI / other provider
```

The upstream provider credential is held only by the gateway. Clients receive gateway credentials instead.

## Local configuration

A typical OpenRouter development `.env` is now:

```dotenv
SAG_PROVIDER=openrouter
SAG_CLIENT_REGISTRY_BACKEND=postgres
SAG_USAGE_LEDGER_BACKEND=postgres
SAG_RATE_LIMIT_BACKEND=redis

POSTGRES_PASSWORD=sag_dev_password
DATABASE_URL=postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway
REDIS_URL=redis://127.0.0.1:6379/0

# Deployment-wide hard ceiling, separate from client profiles.
SAG_ALLOWED_MODELS=openrouter/free

# Non-secret per-client policy registry.
SAG_SECURITY_POLICY_FILE=config/security-policies.json

OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

Create the local policy registry from the tracked example:

```bash
cp config/security-policies.example.json config/security-policies.json
```

`config/security-policies.json`, `.env`, and `.client.env` are ignored by Git.

## Unified security policy profiles

All normal per-client security configuration is grouped in one versioned registry:

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

Multiple clients can share a named profile. The version-1 parser rejects unknown fields, duplicate JSON keys, invalid limits/actions, unknown profile assignments, and malformed identifiers.

Validate the configured file before starting the gateway:

```bash
set -a
source .env
set +a
python -m app.policy_cli validate
```

Expected metadata-only output:

```text
VALID security_policy version=1 profiles=2 clients=1
```

When `SAG_SECURITY_POLICY_FILE` is enabled, it is authoritative. The gateway clears the legacy per-control inputs in its own process before compiling the validated profile registry into the established enforcement modules. If the file is invalid or unavailable, it does not fall back to stale legacy grants; protected requests fail closed.

The policy file is loaded at process startup, so restart the gateway after changing it.

The deployment-wide `SAG_ALLOWED_MODELS` setting deliberately remains outside the profile file. A client profile may narrow model access but cannot widen that hard ceiling.

See `docs/security-policy-profiles.md` for the schema, migration behavior, and security boundaries.

## Local state services

`compose.yaml` provides PostgreSQL and Redis, both bound only to `127.0.0.1` for local development.

```bash
docker compose up -d postgres redis
docker compose ps
```

Redis uses append-only persistence locally. Production deployments still require appropriate authentication, TLS, network isolation, replication, and availability controls.

Load server configuration and apply PostgreSQL migrations with:

```bash
set -a
source .env
set +a
python -m app.database migrate
python -m app.database status
```

Current migrations:

```text
001_client_registry.sql
002_daily_usage.sql
```

## Client identity and API keys

The database stores client identity, public key ID, SHA-256 key digest, activation state, and timestamps. It never stores raw gateway API keys.

For each protected request the gateway validates the structured bearer key, extracts `key_id`, resolves an active key belonging to an active client, hashes the presented key, compares hashes with `hmac.compare_digest`, and returns `Principal(client_id, key_id)` to downstream policy checks.

Client/key administration:

```bash
python -m app.clients list
python -m app.clients create service-a
python -m app.clients revoke <key-id>
python -m app.clients rotate local-dev
```

Rotation creates a replacement key hash and revokes prior active keys in one PostgreSQL transaction. The new raw key is printed once and must be stored on the client side.

## Security controls

### Model authorization

A requested model must pass two independent layers:

```text
SAG_ALLOWED_MODELS deployment ceiling
              AND
profile.allowed_models client grant
```

Failure at either layer returns the same generic denial. Client profiles cannot expand the deployment-wide ceiling.

### Redis distributed rate limiting

`profile.requests_per_minute` supplies the authenticated client's fixed-window quota. Redis stores the shared counter under `sag:rate_limit:<client_id>`, so Uvicorn restarts and additional workers do not reset or split quota. Redis failures fail closed with `503 Rate limiting is unavailable.`

Allowed responses expose `X-RateLimit-Limit` and `X-RateLimit-Remaining`; exhausted clients receive `429` with `Retry-After`.

### Daily token and cost budgets

A profile contains:

```json
"daily_budget": {
  "tokens": 50000,
  "cost_usd": "1.00"
}
```

Use `null` to disable one dimension. Both cannot be disabled simultaneously. Cost is a JSON string so decimal values remain exact.

The gateway reads the current UTC-day total from PostgreSQL before forwarding and atomically increments `gateway_daily_usage` after a successful provider response. The request that crosses the remaining budget is returned and recorded; subsequent requests are blocked until the next UTC day.

### PII detection and redaction

`profile.pii_action` is `redact` or `deny`.

The structured baseline detects email addresses, U.S. Social Security numbers in `NNN-NN-NNNN` form, common North American phone-number formats, and 13-19 digit payment-card candidates that pass a Luhn checksum.

`redact` replaces detected values in a copied request before provider forwarding. `deny` blocks the request before provider work. Raw detected values are not copied into audit records.

This is structured-pattern protection, not comprehensive semantic PII recognition.

### Prompt-injection detection

`profile.prompt_injection_action` is `audit`, `deny`, or `off`.

The deterministic baseline identifies explicit instruction override, system/developer prompt extraction, role impersonation, safety/policy bypass, secret-exfiltration requests, and bounded Base64/hex content that decodes to a direct indicator.

`X-Prompt-Injection-Score` is a deterministic rule score, not a probability or confidence estimate. Raw prompt text and matched fragments are not added to audit records.

This first layer does not claim to catch every indirect injection, novel obfuscation, multi-turn attack, multimodal injection, RAG poisoning, or model-specific jailbreak strategy.

### Function-tool authorization

`profile.allowed_tools` lists exact function names the authenticated client may expose to the model. An empty list means no tools.

There is no wildcard grant. If any declared function is outside the profile allowlist, the request fails with `403` before provider forwarding. A named `tool_choice` must refer to a function declared in the same request and granted by policy.

This authorizes **tool exposure**, not execution. The gateway currently proxies function definitions and model `tool_calls`; it does not execute them. A future executor must independently authenticate context, validate arguments, and re-authorize the specific side effect. Model output is untrusted data, not an authorization decision.

See `docs/tool-authorization.md` for details.

## System-prompt leakage evaluation

The live evaluator inserts only synthetic protected text and a disposable canary into a test system prompt, then sends adversarial extraction attempts directly to the configured provider:

```bash
python -m app.evals.system_prompt_leakage \
  --live \
  --model openrouter/free
```

It reports `PASS`/`FAIL` per case without printing provider response bodies. `--live` is mandatory because the evaluator bypasses gateway token/cost enforcement and makes direct provider requests.

A passing run means no tested synthetic canary leakage was observed. It does not make the system prompt a secrecy or authorization boundary.

See `docs/system-prompt-leakage.md` for details.

## Audit logging

Audit events are emitted as one JSON object per line to application stderr. Safe fields include client/key IDs, model names, rate-limit state, PII type/count metadata, prompt-injection indicator metadata, validated function-tool names/counts, request/cumulative usage, provider name, request ID, and latency.

The audit layer intentionally omits raw gateway keys, prompts/messages, matched injection fragments, detected PII values, provider credentials, function arguments, tool-result content, and upstream response bodies.

## Development setup

Requirements:

- Python 3.13+
- Docker with Compose
- Git

Install or refresh dependencies:

```bash
python -m pip install -e '.[dev]'
```

Create local policy configuration once:

```bash
cp config/security-policies.example.json config/security-policies.json
```

Start state services, load configuration, validate policy, and migrate PostgreSQL:

```bash
docker compose up -d postgres redis
set -a
source .env
set +a
python -m app.policy_cli validate
python -m app.database migrate
pytest -q
```

Start the gateway:

```bash
uvicorn app.main:app --reload --env-file .env
```

In another terminal load the client key:

```bash
set -a
source .client.env
set +a
```

Then call the OpenAI-style route with the configured gateway key.

## Repository layout

```text
Secure-AI-Gateway/
├── app/
│   ├── api_keys.py
│   ├── audit.py
│   ├── auth.py
│   ├── client_registry.py
│   ├── clients.py
│   ├── database.py
│   ├── evals/
│   │   └── system_prompt_leakage.py
│   ├── main.py
│   ├── models.py
│   ├── pii.py
│   ├── policy_cli.py
│   ├── prompt_injection.py
│   ├── rate_limit.py
│   ├── security_policy.py
│   ├── security_policy_bootstrap.py
│   ├── tool_authorization.py
│   ├── usage_budget.py
│   ├── policies/
│   └── providers/
├── config/
│   └── security-policies.example.json
├── db/migrations/
├── docs/
│   ├── security-policy-profiles.md
│   ├── system-prompt-leakage.md
│   └── tool-authorization.md
├── tests/
│   ├── test_pii.py
│   ├── test_prompt_injection.py
│   ├── test_security_policy.py
│   ├── test_system_prompt_leakage.py
│   └── test_tool_authorization.py
├── .client.env.example
├── .env.example
├── compose.yaml
├── pyproject.toml
└── README.md
```

## Roadmap

### Gateway foundation

- [x] FastAPI gateway
- [x] OpenAI-compatible chat-completions route
- [x] provider abstraction
- [x] mock provider
- [x] OpenAI provider
- [x] OpenRouter provider

### Core security controls

- [x] per-client identities and high-entropy API keys
- [x] hashed API-key verification
- [x] PostgreSQL persistent client/key registry
- [x] key revocation and atomic rotation
- [x] global/per-client model authorization
- [x] Redis-backed distributed rate limiting
- [x] daily token/cost budgets
- [x] persistent PostgreSQL usage accounting
- [x] structured audit logging and request correlation
- [x] versioned configurable security-policy profiles

### LLM security controls

- [x] structured PII detection/redaction
- [ ] semantic PII detection/evaluation
- [x] deterministic prompt-injection detection
- [x] system-prompt leakage tests
- [x] least-privilege function-tool authorization
- [ ] execution-time tool authorization

### Evaluation and infrastructure

- [ ] versioned adversarial prompt dataset and measurable detection metrics
- [ ] Dockerized gateway
- [ ] GitHub Actions CI
- [ ] OpenTelemetry / Prometheus
- [ ] Kubernetes
- [ ] Terraform
- [ ] cloud deployment

## Function documentation convention

Project functions use RME-style docstrings where appropriate:

- **Requires** — conditions that must hold before execution
- **Modifies** — state/resources changed
- **Effects** — externally visible side effects
- **Inputs** — function inputs
- **Outputs** — returned or produced outputs

## License

No license has been selected yet.
