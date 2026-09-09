# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends.

It centralizes client authentication, model authorization, rate limiting, token/cost budgets, provider routing, audit logging, and request correlation before a request reaches an upstream model.

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
- per-client requests-per-minute rate limiting
- per-client UTC-day token/cost budgets
- OpenRouter token/cost accounting
- structured one-line JSON audit events with client attribution
- `X-Request-ID` request correlation
- requested-versus-resolved model attribution
- sanitized provider failures
- deterministic `pytest` suite that does not call real providers

Rate-limit counters and daily usage totals are still process-local. Redis-backed rate limiting and persistent usage accounting remain future infrastructure work.

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
  +-- per-client rate limit
  +-- global model allowlist
  +-- per-client model allowlist
  +-- daily token/cost budget
  +-- structured audit logging
  +-- provider routing
  |
  v
OpenRouter / OpenAI / other provider
```

The upstream provider credential is held only by the gateway. Clients receive gateway credentials instead.

## Local configuration

Copy the template:

```bash
cp .env.example .env
```

A typical OpenRouter development configuration is:

```dotenv
SAG_PROVIDER=openrouter
SAG_CLIENT_REGISTRY_BACKEND=postgres

POSTGRES_PASSWORD=sag_dev_password
DATABASE_URL=postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway

SAG_ALLOWED_MODELS=openrouter/free
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free
SAG_CLIENT_RATE_LIMITS=local-dev:10
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-

OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

Use `-` to disable one usage-budget dimension. For example:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-
```

means 50,000 tokens per UTC day with no gateway dollar-cost ceiling.

`.env` and `.client.env` are ignored by Git.

## PostgreSQL client registry

Runtime authentication defaults to PostgreSQL. The database stores only client identity, public key ID, SHA-256 key digest, activation state, and timestamps. It never stores the raw gateway key.

The schema separates clients from API keys:

```text
gateway_clients
  client_id
  is_active
  created_at
  updated_at

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

That layout supports multiple keys per client, immediate revocation, and transactional key rotation.

### Start PostgreSQL

The repository includes `compose.yaml` with PostgreSQL bound to local loopback only:

```bash
docker compose up -d postgres
```

Check it:

```bash
docker compose ps
```

### Initialize the schema

Load your server environment into the shell:

```bash
set -a
source .env
set +a
```

Then run:

```bash
python -m app.clients init-db
```

Expected:

```text
Client registry schema initialized.
```

### Migrate an existing `SAG_CLIENTS` key without changing the raw client key

If upgrading from the earlier environment-backed client registry, temporarily keep your existing line in `.env`:

```dotenv
SAG_CLIENTS=local-dev:<key-id>:<sha256>
```

Load `.env`, then import it:

```bash
set -a
source .env
set +a
python -m app.clients import-env
```

Expected:

```text
Imported 1 client key record(s).
```

Verify only non-secret metadata is stored:

```bash
python -m app.clients list
```

Example:

```text
client_id=local-dev key_id=abcd1234 client_active=true key_active=true
```

After that succeeds, remove `SAG_CLIENTS` from `.env`. The raw key in `.client.env` does not change.

### Create a new database-backed client key

For a new client:

```bash
python -m app.clients create service-a
```

The command prints the raw key once. PostgreSQL stores only its SHA-256 digest.

Keep the raw key on the client side, for example:

```dotenv
SAG_CLIENT_API_KEY=sag_<key-id>_<secret>
```

## API-key revocation and rotation

List non-secret client/key metadata:

```bash
python -m app.clients list
```

Revoke one key by its public key ID:

```bash
python -m app.clients revoke <key-id>
```

A revoked key remains in PostgreSQL for attribution/history but is excluded from authentication immediately. Repeating the same revoke command is safe and reports that the key was already revoked.

Rotate all active keys for a client:

```bash
python -m app.clients rotate local-dev
```

Rotation runs in one PostgreSQL transaction. It creates a replacement key hash and revokes all previously active keys for that client before committing. The command prints the new raw key exactly once; PostgreSQL never stores that raw value.

After rotation, replace the client-side credential in `.client.env`:

```dotenv
SAG_CLIENT_API_KEY=sag_<new-key-id>_<new-secret>
```

Do not discard the printed raw replacement key until the client-side secret store has been updated. Because only the hash is persisted, the raw key cannot be recovered from PostgreSQL later.

## Authentication behavior

For each protected request the gateway:

1. validates the structured bearer-key format;
2. extracts the public `key_id`;
3. queries PostgreSQL for an active key belonging to an active client;
4. hashes the presented complete key;
5. compares the hash with `hmac.compare_digest`; and
6. returns a `Principal(client_id, key_id)` to downstream policy checks.

Database failures are sanitized and fail closed with `503`. Unknown, inactive, or incorrect keys return `401`.

The test suite explicitly uses the legacy environment registry backend so tests stay deterministic and do not require Docker/PostgreSQL.

## Per-client model authorization

A requested model must pass both layers:

```text
SAG_ALLOWED_MODELS
        |
        v
SAG_CLIENT_ALLOWED_MODELS
        |
        v
     provider
```

Example:

```dotenv
SAG_ALLOWED_MODELS=openrouter/free,other-model
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free
```

`other-model` is globally enabled but unavailable to `local-dev`.

## Per-client rate limiting

Configure fixed requests-per-minute limits:

```dotenv
SAG_CLIENT_RATE_LIMITS=local-dev:10,service-a:60
```

Allowed responses include:

```text
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9
```

Exhausted clients receive `429 Too Many Requests` with `Retry-After`.

The current limiter is process-local. Redis-backed enforcement is required before multi-worker or multi-replica deployment.

## Daily token and cost budgets

Format:

```text
client_id:daily_tokens:daily_cost_usd
```

Examples:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00
SAG_CLIENT_DAILY_BUDGETS=service-a:-:5.00
```

OpenRouter supplies provider-reported token counts and request cost when usage accounting is requested. Free models normally report zero cost while still consuming tokens.

The current daily ledger resets at UTC midnight and is process-local. Restarting the gateway resets accumulated usage until persistent usage accounting is implemented.

## Audit logging

Audit events are emitted as one JSON object per line to application stderr, so they appear in the terminal running Uvicorn.

They can include safe fields such as:

```json
{
  "event": "chat_completion",
  "outcome": "success",
  "client_id": "local-dev",
  "key_id": "abcd1234",
  "provider": "openrouter",
  "requested_model": "openrouter/free",
  "resolved_model": "provider/model:free",
  "request_id": "req_..."
}
```

The audit layer intentionally omits raw gateway keys, prompts/messages, provider credentials, and provider response bodies.

## Development setup

Requirements:

- Python 3.13+
- Docker with Compose
- Git

Install or refresh dependencies after pulling changes:

```bash
python -m pip install -e '.[dev]'
```

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Run tests:

```bash
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

Then test OpenRouter:

```bash
curl -i \
  -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $SAG_CLIENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/free",
    "messages": [
      {"role": "user", "content": "Reply with exactly: PostgreSQL auth works"}
    ]
  }'
```

## Repository layout

```text
Secure-AI-Gateway/
├── app/
│   ├── api_keys.py
│   ├── audit.py
│   ├── auth.py
│   ├── client_registry.py
│   ├── clients.py
│   ├── main.py
│   ├── models.py
│   ├── rate_limit.py
│   ├── usage_budget.py
│   ├── policies/
│   └── providers/
├── db/
│   └── migrations/
│       └── 001_client_registry.sql
├── tests/
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

- [x] per-client identities
- [x] structured/high-entropy API keys
- [x] hashed API-key verification
- [x] PostgreSQL persistent client/key registry
- [x] key revocation and rotation workflow
- [x] global model allowlist
- [x] per-client model policy
- [x] per-client rate limiting
- [x] daily token/cost budgets
- [x] structured audit logging
- [x] request correlation
- [ ] persistent usage accounting
- [ ] Redis-backed distributed rate limiting

### LLM security controls

- [ ] PII detection/redaction
- [ ] prompt-injection detection
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
