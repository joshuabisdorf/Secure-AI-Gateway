# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, distributed request throttling, usage budgets, provider routing, audit logging, and request correlation before requests reach an upstream model.

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
- OpenRouter token/cost accounting
- structured one-line JSON audit events with client attribution
- `X-Request-ID` request correlation
- requested-versus-resolved model attribution
- sanitized provider failures
- deterministic `pytest` suite that does not call real providers, PostgreSQL, or Redis

The next security milestone is PII detection and redaction before prompts are forwarded upstream.

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

OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

Use `-` to disable one usage-budget dimension. For example:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-
```

means 50,000 tokens per UTC day with no gateway dollar-cost ceiling. `.env` and `.client.env` are ignored by Git.

## Local state services

`compose.yaml` provides PostgreSQL 18 and Redis 8.10.1. Both are bound only to `127.0.0.1` for local development.

Start them together:

```bash
docker compose up -d postgres redis
docker compose ps
```

Redis uses append-only persistence in the local Compose service. Production deployments still need appropriate Redis authentication, TLS, network isolation, replication, and availability configuration.

## PostgreSQL schema migrations

Load server configuration:

```bash
set -a
source .env
set +a
```

Apply pending migrations:

```bash
python -m app.database migrate
```

Inspect migration status:

```bash
python -m app.database status
```

Migrations are ordered by filename and tracked in `schema_migrations` with a SHA-256 digest. The runner refuses to continue if an already-applied migration file has been modified.

Current migrations:

```text
001_client_registry.sql
002_daily_usage.sql
```

## PostgreSQL client registry

The database stores only client identity, public key ID, SHA-256 key digest, activation state, and timestamps. It never stores raw gateway API keys.

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

For each protected request the gateway validates the structured bearer key, extracts `key_id`, queries PostgreSQL for an active key belonging to an active client, hashes the complete presented key, compares hashes with `hmac.compare_digest`, and returns `Principal(client_id, key_id)` to downstream policy checks.

Database failures fail closed with sanitized `503` responses. Unknown, inactive, or incorrect keys return `401`.

### Client/key commands

```bash
python -m app.clients list
python -m app.clients create service-a
python -m app.clients revoke <key-id>
python -m app.clients rotate local-dev
```

Rotation creates a replacement key hash and revokes prior active keys in one PostgreSQL transaction. The new raw key is printed once and must be stored on the client side, such as in `.client.env`.

## Model authorization

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

## Redis distributed rate limiting

Configure the backend and per-client requests-per-minute limits:

```dotenv
SAG_RATE_LIMIT_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
SAG_CLIENT_RATE_LIMITS=local-dev:10,service-a:60
```

The runtime limiter uses Redis `INCREX`, available in Redis 8.8+, to atomically increment a fixed-window counter, enforce the configured upper bound, and set the window expiration only when a new window is created.

Each client uses a Redis key of the form:

```text
sag:rate_limit:<client_id>
```

Allowed responses include:

```text
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9
```

When the shared counter has reached the limit, the gateway returns:

```text
HTTP/1.1 429 Too Many Requests
Retry-After: <seconds-until-window-reset>
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
```

Because the counter lives in Redis, Uvicorn restarts and additional gateway workers do not reset or split the request quota. If Redis cannot evaluate the decision, the gateway fails closed with `503 Rate limiting is unavailable.` rather than forwarding without enforcement.

The in-memory limiter remains available only as the deterministic test backend through `SAG_RATE_LIMIT_BACKEND=memory`.

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

OpenRouter supplies provider-reported token counts and request cost when usage accounting is requested. Free models normally report zero cost while still consuming tokens.

The gateway reads the current UTC-day total from PostgreSQL before forwarding. After a successful provider response, it atomically increments:

```text
gateway_daily_usage
  client_id
  usage_date
  tokens_used
  cost_used_usd
  updated_at
```

The primary key is `(client_id, usage_date)`, so each client has one cumulative row per UTC day. Concurrent completions use an atomic PostgreSQL upsert and cannot lose increments through a process-local read/modify/write race.

Exact usage is only known after the provider responds. Therefore the request that crosses the remaining budget is returned and recorded; subsequent requests are blocked until the next UTC day.

Successful responses can include `X-Usage-Tokens-Used`, `X-Usage-Tokens-Remaining`, `X-Usage-Cost-USD`, `X-Usage-Cost-Remaining-USD`, and `X-Usage-Budget-Reset`.

If persistent usage state cannot be read, the gateway returns `503` before forwarding. If an upstream completion succeeds but its usage cannot be persisted, the gateway also fails closed and emits an audit error rather than silently losing accounting data.

## Audit logging

Audit events are emitted as one JSON object per line to application stderr, so they appear in the terminal running Uvicorn. Safe fields include client/key IDs, model names, rate-limit state, request/cumulative usage, provider name, request ID, and latency.

The audit layer intentionally omits raw gateway keys, prompts/messages, provider credentials, and upstream response bodies.

## Development setup

Requirements:

- Python 3.13+
- Docker with Compose
- Git

Install or refresh dependencies:

```bash
python -m pip install -e '.[dev]'
```

Start state services and migrate PostgreSQL:

```bash
docker compose up -d postgres redis
set -a
source .env
set +a
python -m app.database migrate
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
      {"role": "user", "content": "Reply with exactly: Distributed rate limit works"}
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
│   ├── database.py
│   ├── main.py
│   ├── models.py
│   ├── rate_limit.py
│   ├── usage_budget.py
│   ├── policies/
│   └── providers/
├── db/
│   └── migrations/
│       ├── 001_client_registry.sql
│       └── 002_daily_usage.sql
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
- [x] Redis-backed distributed rate limiting
- [x] daily token and cost budgets
- [x] persistent PostgreSQL usage accounting
- [x] structured audit logging
- [x] request correlation

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
