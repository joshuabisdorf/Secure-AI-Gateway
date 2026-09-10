# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, distributed request throttling, usage budgets, sensitive-data controls, prompt-injection controls, tool exposure policy, provider routing, audit logging, and request correlation before requests reach an upstream model.

## Current capabilities

Implemented:

- FastAPI `/health` and OpenAI-style `/v1/chat/completions`
- deterministic non-network mock provider
- OpenAI and OpenRouter upstream providers
- structured high-entropy gateway API keys
- SHA-256 API-key verification without storing raw gateway keys
- PostgreSQL-backed persistent client/key registry
- database-backed API-key revocation and atomic rotation
- deployment-wide and per-client model allowlists
- Redis-backed per-client requests-per-minute rate limiting
- per-client UTC-day token/cost budgets
- PostgreSQL-backed persistent daily usage accounting
- per-client structured-PII `redact` and `deny` policies
- pre-provider redaction for email, U.S. SSN, common North American phone, and Luhn-valid payment-card values
- per-client prompt-injection `audit`, `deny`, and explicit `off` policies
- deterministic direct and encoded prompt-injection indicators with safe audit metadata
- synthetic system-prompt leakage evaluation with disposable canaries
- per-client function-tool allowlists with explicit no-tool posture
- OpenAI-compatible function `tools`, `tool_choice`, tool-result messages, and assistant `tool_calls`
- structured one-line JSON audit events with client attribution
- `X-Request-ID` request correlation
- requested-versus-resolved model attribution
- sanitized provider failures
- deterministic `pytest` suite that does not call real providers, PostgreSQL, or Redis

The next security milestone is consolidating the individual environment policies into a configurable security-policy layer, followed by versioned adversarial evaluation and measurable detection metrics.

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
  +-- per-client function-tool authorization
  +-- Redis per-client rate limit
  +-- global model allowlist
  +-- per-client model allowlist
  +-- per-client PII inspection / redaction or denial
  +-- per-client prompt-injection inspection / audit or denial
  +-- PostgreSQL daily token/cost accounting
  +-- structured audit logging
  +-- provider routing
  |
  v
OpenRouter / OpenAI / other provider
```

The upstream provider credential is held only by the gateway. Clients receive gateway credentials instead.

## Local configuration

A typical OpenRouter development configuration is:

```dotenv
SAG_PROVIDER=openrouter
SAG_CLIENT_REGISTRY_BACKEND=postgres
SAG_USAGE_LEDGER_BACKEND=postgres
SAG_RATE_LIMIT_BACKEND=redis

POSTGRES_PASSWORD=sag_dev_password
DATABASE_URL=postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway
REDIS_URL=redis://127.0.0.1:6379/0

SAG_ALLOWED_MODELS=openrouter/free
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free
SAG_CLIENT_RATE_LIMITS=local-dev:10
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00
SAG_CLIENT_PII_POLICIES=local-dev:redact
SAG_CLIENT_PROMPT_INJECTION_POLICIES=local-dev:audit
SAG_CLIENT_ALLOWED_TOOLS=local-dev:-

OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

Use `-` to disable one usage-budget dimension. For example, `local-dev:50000:-` means 50,000 tokens per UTC day with no gateway dollar-cost ceiling. For tool authorization, `local-dev:-` has a different meaning: the client is explicitly configured to expose no functions to a model.

`.env` and `.client.env` are ignored by Git.

## Local state services

`compose.yaml` provides PostgreSQL 18 and Redis. Both are bound only to `127.0.0.1` for local development.

```bash
docker compose up -d postgres redis
docker compose ps
```

Redis uses append-only persistence locally. Production deployments still require appropriate authentication, TLS, network isolation, replication, and availability controls.

## PostgreSQL schema migrations

Load server configuration and apply pending migrations:

```bash
set -a
source .env
set +a
python -m app.database migrate
python -m app.database status
```

Migrations are ordered by filename and tracked in `schema_migrations` with SHA-256 digests. The runner refuses to continue if an already-applied migration file has been modified.

Current migrations:

```text
001_client_registry.sql
002_daily_usage.sql
```

## PostgreSQL client registry

The database stores client identity, public key ID, SHA-256 key digest, activation state, and timestamps. It never stores raw gateway API keys.

```text
gateway_clients
  client_id
  is_active

        1
        |
        | many
        v

gateway_api_keys
  key_id
  client_id
  api_key_sha256
  is_active
  created_at
  revoked_at
```

For each protected request the gateway validates the structured bearer key, extracts `key_id`, queries PostgreSQL for an active key belonging to an active client, hashes the complete presented key, compares hashes with `hmac.compare_digest`, and returns `Principal(client_id, key_id)` to downstream policy checks.

Client/key administration:

```bash
python -m app.clients list
python -m app.clients create service-a
python -m app.clients revoke <key-id>
python -m app.clients rotate local-dev
```

Rotation creates a replacement key hash and revokes prior active keys in one PostgreSQL transaction. The new raw key is printed once and must be stored on the client side.

## Function-tool authorization

Function-tool exposure is a permission boundary. A model can request only functions that the authenticated client was allowed to expose in the request.

Tool policy is mandatory for valid protected chat requests. Explicitly grant no tools with:

```dotenv
SAG_CLIENT_ALLOWED_TOOLS=local-dev:-
```

Grant individual functions by exact name:

```dotenv
SAG_CLIENT_ALLOWED_TOOLS=agent-a:search,agent-a:calculator,agent-b:lookup_ticket
```

There is no wildcard grant. If any declared function is outside the client's allowlist, the entire request fails before provider forwarding:

```text
HTTP/1.1 403 Forbidden
X-Tool-Authorization-Action: denied
X-Tool-Requested-Count: 1
```

```json
{"detail":"Requested tool is not allowed."}
```

Authorized exposure includes:

```text
X-Tool-Authorization-Action: allowed
X-Tool-Requested-Count: 1
```

A named `tool_choice` must identify a function declared in the same request and permitted for that client. Missing, malformed, or client-incomplete tool policy fails closed with `503`.

This control authorizes **tool exposure**, not execution. The gateway currently proxies function definitions and model `tool_calls`; it does not execute them. A future tool executor must independently authenticate the context, validate arguments, and re-authorize the specific action before causing side effects. Model output is untrusted data, not an authorization decision.

Only function tools are supported by this milestone. Provider-native built-in tools and MCP tools require separate policy boundaries.

See `docs/tool-authorization.md` for the detailed design and examples.

## Model authorization

A model must be present in both the deployment-wide ceiling and the authenticated client's grant:

```dotenv
SAG_ALLOWED_MODELS=openrouter/free,other-model
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free
```

This lets the deployment expose `other-model` while preventing `local-dev` from using it.

## Redis distributed rate limiting

```dotenv
SAG_RATE_LIMIT_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
SAG_CLIENT_RATE_LIMITS=local-dev:10,service-a:60
```

The runtime limiter stores each client's fixed-window counter in Redis under `sag:rate_limit:<client_id>`. Counters survive Uvicorn restarts and are shared by gateway workers/replicas. Allowed responses expose `X-RateLimit-Limit` and `X-RateLimit-Remaining`; exhausted clients receive `429` with `Retry-After`. Redis failures fail closed with `503 Rate limiting is unavailable.`

The in-memory limiter remains only as the deterministic test backend through `SAG_RATE_LIMIT_BACKEND=memory`.

## Daily token and cost budgets

Budget format:

```text
client_id:daily_tokens:daily_cost_usd
```

Examples:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00
SAG_CLIENT_DAILY_BUDGETS=service-a:-:5.00
```

OpenRouter supplies provider-reported token counts and request cost when usage accounting is requested. The gateway reads the current UTC-day total from PostgreSQL before forwarding and atomically increments `gateway_daily_usage` after a successful provider response.

Exact usage is known only after completion. Therefore the request that crosses the remaining budget is returned and recorded; subsequent requests are blocked until the next UTC day.

Successful responses can include `X-Usage-Tokens-Used`, `X-Usage-Tokens-Remaining`, `X-Usage-Cost-USD`, `X-Usage-Cost-Remaining-USD`, and `X-Usage-Budget-Reset`.

## PII detection and redaction

Per-client PII policy is mandatory for protected chat requests:

```dotenv
SAG_CLIENT_PII_POLICIES=local-dev:redact,high-security-client:deny
```

Supported actions:

```text
redact  detect structured PII, replace values in a copied request, and forward only the sanitized copy
deny    reject a request containing detected structured PII before any provider call
```

Missing, malformed, or client-incomplete PII policy fails closed with `503`.

The current baseline detects email addresses, U.S. Social Security numbers in `NNN-NN-NNNN` form, common North American phone-number formats, and 13-19 digit payment-card candidates that pass a Luhn checksum.

Redaction placeholders are type-specific:

```text
[REDACTED_EMAIL]
[REDACTED_SSN]
[REDACTED_PHONE]
[REDACTED_PAYMENT_CARD]
```

PII inspection happens before prompt-injection inspection and provider work. Raw detected values are not copied into audit records.

This is structured-pattern protection, not comprehensive PII recognition. It does not yet claim to reliably identify names, street addresses, dates of birth, medical details, arbitrary account identifiers, or identity information implied by natural language.

## Prompt-injection detection

Per-client prompt-injection policy is mandatory for protected chat requests:

```dotenv
SAG_CLIENT_PROMPT_INJECTION_POLICIES=local-dev:audit,high-security-client:deny
```

Supported actions:

```text
audit  inspect, record safe indicator metadata, and forward even when indicators are found
deny   inspect and block when one or more indicators are found
off    explicitly skip inspection for that client
```

`off` is an explicit opt-out. Missing, malformed, or client-incomplete policy fails closed with `503 Prompt injection policy is not configured.`

The deterministic baseline detects named indicators for instruction override, system/developer prompt extraction, role impersonation, safety/policy bypass, secret exfiltration, and bounded Base64/hex content that decodes to a direct indicator.

`X-Prompt-Injection-Score` is a deterministic rule score, not a probability or confidence estimate. Raw prompt text and matched fragments are not added to audit records.

This is a deterministic first layer, not comprehensive prompt-injection prevention. It does not claim to catch every indirect injection, novel obfuscation, multi-turn attack, best-of-N jailbreak, multimodal injection, RAG poisoning, or model-specific strategy.

## System-prompt leakage evaluation

The repository includes a live evaluator that inserts only synthetic protected text and a disposable canary into a test system prompt, then sends adversarial extraction attempts directly to the configured provider:

```bash
python -m app.evals.system_prompt_leakage \
  --live \
  --model openrouter/free
```

It reports `PASS`/`FAIL` per case and does not print provider response bodies. `--live` is mandatory because this evaluation makes direct upstream requests and bypasses gateway token/cost enforcement.

A passing run means no tested synthetic canary leakage was observed. It does **not** establish system prompts as a secrecy or authorization boundary. Real credentials and authorization decisions remain outside model instructions.

See `docs/system-prompt-leakage.md` for details.

## Audit logging

Audit events are emitted as one JSON object per line to application stderr. Safe fields include client/key IDs, model names, rate-limit state, PII count/type metadata, prompt-injection indicator metadata, validated function-tool names/counts, request/cumulative usage, provider name, request ID, and latency.

The audit layer intentionally omits raw gateway keys, prompts/messages, matched injection fragments, detected PII values, provider credentials, function arguments, tool-result content, and upstream response bodies.

## Development setup

Requirements:

- Python 3.13+
- Docker with Compose
- Git

Install or refresh dependencies and start state services:

```bash
python -m pip install -e '.[dev]'
docker compose up -d postgres redis
set -a
source .env
set +a
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
│   ├── prompt_injection.py
│   ├── rate_limit.py
│   ├── tool_authorization.py
│   ├── usage_budget.py
│   ├── policies/
│   └── providers/
├── db/migrations/
├── docs/
│   ├── system-prompt-leakage.md
│   └── tool-authorization.md
├── tests/
│   ├── test_pii.py
│   ├── test_prompt_injection.py
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

### LLM security controls

- [x] structured PII detection/redaction
- [ ] semantic PII detection/evaluation
- [x] deterministic prompt-injection detection
- [x] system-prompt leakage tests
- [x] least-privilege function-tool authorization
- [ ] execution-time tool authorization
- [ ] configurable security policies

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
