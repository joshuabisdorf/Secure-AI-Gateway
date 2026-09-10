# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, distributed request throttling, usage budgets, sensitive-data controls, prompt-injection controls, provider routing, audit logging, and request correlation before requests reach an upstream model.

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
- structured one-line JSON audit events with client attribution
- `X-Request-ID` request correlation
- requested-versus-resolved model attribution
- sanitized provider failures
- deterministic `pytest` suite that does not call real providers, PostgreSQL, or Redis

The next LLM-security milestone is system-prompt leakage testing.

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

OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

Use `-` to disable one usage-budget dimension. For example, `local-dev:50000:-` means 50,000 tokens per UTC day with no gateway dollar-cost ceiling. `.env` and `.client.env` are ignored by Git.

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

The current baseline detects:

- email addresses
- U.S. Social Security numbers in `NNN-NN-NNNN` form
- common North American phone-number formats
- 13-19 digit payment-card candidates that pass a Luhn checksum

Redaction placeholders are type-specific:

```text
[REDACTED_EMAIL]
[REDACTED_SSN]
[REDACTED_PHONE]
[REDACTED_PAYMENT_CARD]
```

A redacted allowed request includes headers such as:

```text
X-PII-Action: redacted
X-PII-Detected-Count: 1
```

A `deny` policy returns:

```text
HTTP/1.1 403 Forbidden
X-PII-Action: denied
X-PII-Detected-Count: 1
```

with:

```json
{"detail":"Request contains prohibited sensitive data."}
```

PII inspection runs after authentication, rate limiting, and model authorization, but before prompt-injection, usage-budget, and provider work. Therefore a denied request consumes request-rate capacity but does not incur model tokens or provider cost.

Audit events may record only safe metadata such as:

```json
{
  "event": "pii_policy",
  "outcome": "redact",
  "client_id": "local-dev",
  "pii_detected_count": 2,
  "pii_types": "email,phone"
}
```

Raw detected values are not copied into audit records.

### Detection boundary

This is structured-pattern protection, not comprehensive PII recognition. It does not yet claim to reliably identify names, street addresses, dates of birth, medical details, arbitrary account identifiers, or identity information implied by natural language. Those require semantic/NER detection plus measurable false-positive and false-negative evaluation. The deterministic structured layer remains useful even after a semantic detector is added.

## Prompt-injection detection

Per-client prompt-injection policy is mandatory for protected chat requests:

```dotenv
SAG_CLIENT_PROMPT_INJECTION_POLICIES=local-dev:audit,high-security-client:deny
```

Supported actions:

```text
audit  inspect the request, record safe detection metadata, and forward even when indicators are found
deny   inspect the request and block it when one or more indicators are found
off    explicitly skip prompt-injection inspection for that client
```

`off` is an explicit opt-out. Missing, malformed, or client-incomplete policy is different and fails closed with `503 Prompt injection policy is not configured.`

The deterministic baseline currently detects named indicators for:

- attempts to ignore, disregard, forget, or override prior/system/developer instructions
- attempts to reveal or reproduce system/developer/hidden instructions
- common developer/admin/root/unrestricted role impersonation
- explicit safety/security/policy/guardrail bypass language
- explicit attempts to extract API keys, passwords, secrets, credentials, or tokens
- bounded Base64 or hexadecimal payloads that decode to one of the direct indicators above

Encoded strings are not flagged merely for looking encoded. A bounded candidate must decode successfully and the decoded text must match a direct injection indicator.

Each unique indicator contributes a fixed rule weight. `X-Prompt-Injection-Score` is therefore a deterministic rule score, not a probability, confidence estimate, or model judgment.

In `audit` mode, a detected request can include:

```text
HTTP/1.1 200 OK
X-Prompt-Injection-Action: audited
X-Prompt-Injection-Detected-Count: 2
X-Prompt-Injection-Score: 8
```

In `deny` mode, the same request is blocked before budget/provider work:

```text
HTTP/1.1 403 Forbidden
X-Prompt-Injection-Action: denied
X-Prompt-Injection-Detected-Count: 2
X-Prompt-Injection-Score: 8
```

with:

```json
{"detail":"Potential prompt injection detected."}
```

Clean inspected requests use `X-Prompt-Injection-Action: none`. Explicit `off` mode uses `X-Prompt-Injection-Action: off` with zero detected indicators/score.

Prompt-injection inspection runs on the request after any PII redaction. A denied injection attempt consumes request-rate capacity but does not consume model tokens or provider cost.

Audit events store only safe metadata such as indicator labels, count, and score:

```json
{
  "event": "prompt_injection",
  "outcome": "audit",
  "client_id": "local-dev",
  "reason": "prompt_injection_detected",
  "prompt_injection_detected_count": 2,
  "prompt_injection_score": 8,
  "prompt_injection_indicators": "instruction_override,system_prompt_extraction"
}
```

Raw prompt text and matched prompt fragments are not added to audit records.

### Detection boundary

This is a deterministic first layer, not comprehensive prompt-injection prevention. It does not claim to reliably catch every indirect injection hidden in external content, typoglycemic or novel obfuscation, multi-turn attacks, best-of-N jailbreaks, multimodal injection, RAG poisoning, or model-specific jailbreak strategy. Later evaluation should measure attack detection and false-positive rates against a versioned adversarial dataset. A purpose-trained guardrail/classifier can be added as another layer without removing deterministic controls.

## Audit logging

Audit events are emitted as one JSON object per line to application stderr. Safe fields include client/key IDs, model names, rate-limit state, PII count/type metadata, prompt-injection indicator metadata, request/cumulative usage, provider name, request ID, and latency.

The audit layer intentionally omits raw gateway keys, prompts/messages, matched injection fragments, detected PII values, provider credentials, and upstream response bodies.

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
│   ├── main.py
│   ├── models.py
│   ├── pii.py
│   ├── prompt_injection.py
│   ├── rate_limit.py
│   ├── usage_budget.py
│   ├── policies/
│   └── providers/
├── db/migrations/
├── tests/
│   ├── test_pii.py
│   └── test_prompt_injection.py
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
- [ ] system-prompt leakage tests
- [ ] tool authorization
- [ ] configurable security policies

### Evaluation and infrastructure

- [ ] adversarial prompt dataset and measurable detection metrics
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
