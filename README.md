# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, distributed request throttling, usage budgets, sensitive-data controls, prompt-injection controls, tool exposure policy, provider routing, audit logging, request correlation, and security evaluation before requests reach an upstream model.

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
- versioned offline adversarial prompt-injection benchmark with precision, recall, FPR, FNR, and category metrics
- Dockerized gateway with PostgreSQL/Redis Compose integration and non-root runtime hardening
- structured one-line JSON audit events with client attribution and request correlation
- sanitized provider failures
- deterministic `pytest` suite that does not call real providers, PostgreSQL, or Redis

The next infrastructure milestone is GitHub Actions CI that enforces the test suite, prompt-injection benchmark gate, and container build. Semantic PII detection/evaluation and execution-time tool authorization remain explicit security milestones after that foundation.

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

A typical OpenRouter development `.env` is:

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

Create the ignored local policy registry once:

```bash
cp config/security-policies.example.json config/security-policies.json
```

`config/security-policies.json`, `.env`, and `.client.env` are ignored by Git and excluded from the Docker build context.

## Dockerized local stack

The default integration workflow runs the gateway, PostgreSQL, and Redis through Compose:

```bash
docker compose build gateway
docker compose up -d
docker compose ps
```

The gateway is exposed only on `127.0.0.1:8000`; PostgreSQL and Redis retain their loopback-only development ports. Inside the Compose network, the gateway uses `postgres:5432` and `redis:6379`, so the host-side `DATABASE_URL` and `REDIS_URL` can remain unchanged for direct development.

Compose waits for PostgreSQL and Redis to become healthy before starting the gateway. The tracked container entrypoint validates the unified security policy and applies idempotent PostgreSQL migrations before starting Uvicorn.

The gateway container runs as a dedicated non-root user, uses a read-only root filesystem, drops all Linux capabilities, enables `no-new-privileges`, and receives only a small writable `/tmp` tmpfs. The local policy file is bind-mounted read-only.

Verify process health:

```bash
curl -i http://127.0.0.1:8000/health
```

For routine cleanup, preserve persistent database/rate-limit state:

```bash
docker compose down
```

Do not use `docker compose down -v` unless intentionally deleting PostgreSQL and Redis data.

See `docs/docker.md` for the complete container workflow and security boundaries.

## Unified security policy profiles

Normal per-client policy is grouped in one versioned registry:

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

Validate host-side configuration with:

```bash
set -a
source .env
set +a
python -m app.policy_cli validate
```

When `SAG_SECURITY_POLICY_FILE` is enabled, it is authoritative. Invalid or unavailable unified policy does not fall back to stale legacy grants; protected requests fail closed. The deployment-wide `SAG_ALLOWED_MODELS` setting remains independent, so a client profile can narrow model access but cannot widen the hard ceiling.

See `docs/security-policy-profiles.md` for the schema and migration behavior.

## Persistent state

PostgreSQL stores client/key records and daily usage. Redis stores distributed fixed-window request counters. Current database migrations are:

```text
001_client_registry.sql
002_daily_usage.sql
```

For host-run development, apply migrations with:

```bash
set -a
source .env
set +a
python -m app.database migrate
python -m app.database status
```

The Docker gateway entrypoint runs the same migration command before Uvicorn. A future multi-replica production deployment should move migrations to a dedicated deployment/init job instead of running them concurrently in every replica.

Client/key administration remains host-side:

```bash
python -m app.clients list
python -m app.clients create service-a
python -m app.clients revoke <key-id>
python -m app.clients rotate local-dev
```

Raw gateway keys are never stored in PostgreSQL.

## Security controls

### Model authorization

A requested model must pass both the deployment-wide `SAG_ALLOWED_MODELS` ceiling and `profile.allowed_models`. Failure at either layer returns the same generic denial.

### Distributed rate limiting

`profile.requests_per_minute` supplies the authenticated client's fixed-window quota. Redis stores the shared counter under `sag:rate_limit:<client_id>`, so Uvicorn restarts and additional workers do not reset or split quota. Redis failures fail closed.

### Daily token and cost budgets

Profiles configure daily token and/or cost limits. The gateway reads the current UTC-day total from PostgreSQL before forwarding and atomically records provider-reported usage after completion. The request that crosses the remaining budget is returned and recorded; subsequent requests are blocked until the next UTC day.

### PII detection and redaction

`profile.pii_action` is `redact` or `deny`. The structured baseline detects email addresses, U.S. SSNs, common North American phone formats, and Luhn-valid payment-card candidates. Raw detected values are not copied into audit records. This remains structured-pattern protection, not comprehensive semantic PII recognition.

### Prompt-injection detection

`profile.prompt_injection_action` is `audit`, `deny`, or `off`. The deterministic baseline identifies direct instruction override, system/developer prompt extraction, role impersonation, safety/policy bypass, secret-exfiltration requests, and bounded Base64/hex content that decodes to a direct indicator. The score is a deterministic rule score, not a probability.

### Function-tool authorization

`profile.allowed_tools` lists exact function names the authenticated client may expose to the model. There is no wildcard grant. An empty list means no tools. This authorizes tool exposure only; the gateway does not execute tool calls. Any future executor must independently authenticate context, validate arguments, and re-authorize the side effect before execution.

See `docs/tool-authorization.md` for details.

## Security evaluation

### System-prompt leakage

The live evaluator inserts only synthetic protected text and a disposable canary into a test system prompt:

```bash
python -m app.evals.system_prompt_leakage \
  --live \
  --model openrouter/free
```

It reports `PASS`/`FAIL` without printing provider response bodies. A passing run means no tested synthetic canary leakage was observed; it does not make system prompts a secrecy or authorization boundary.

### Prompt-injection benchmark

The committed version-1 benchmark evaluates the deterministic detector completely offline:

```bash
python -m app.evals.prompt_injection_benchmark
python -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
```

The dataset contains 66 synthetic cases and reports a confusion matrix, precision, recall, false-positive rate, false-negative rate, category detection rates, and a dataset SHA-256 digest.

The version-1 baseline when introduced is:

```text
TP=36  FP=7  TN=13  FN=10
precision=0.8372
recall=0.7826
false_positive_rate=0.3500
false_negative_rate=0.2174
```

Those metrics deliberately expose current weaknesses, including typoglycemia, split multi-turn attacks, and false positives on quoted/descriptive security text. See `docs/prompt-injection-benchmark.md` for metric definitions and versioning rules.

## Audit logging

Audit events are emitted as one JSON object per line to application stderr. Safe fields include client/key IDs, model names, rate-limit state, PII type/count metadata, prompt-injection indicator metadata, validated function-tool names/counts, request/cumulative usage, provider name, request ID, and latency.

The audit layer intentionally omits raw gateway keys, prompts/messages, matched injection fragments, detected PII values, provider credentials, function arguments, tool-result content, and upstream response bodies.

## Development setup

Requirements:

- Python 3.13+
- Docker with Compose
- Git

Run deterministic host-side tests and the benchmark:

```bash
python -m pip install -e '.[dev]'
pytest -q
python -m app.evals.prompt_injection_benchmark --enforce-baseline
```

For direct host-side gateway development, start only the state services:

```bash
docker compose up -d postgres redis
set -a
source .env
set +a
python -m app.policy_cli validate
python -m app.database migrate
uvicorn app.main:app --reload --env-file .env
```

For integration testing of the containerized gateway instead:

```bash
docker compose up -d --build
```

Load the client credential in the host shell before authenticated requests:

```bash
set -a
source .client.env
set +a
```

## Repository layout

```text
Secure-AI-Gateway/
├── app/
│   ├── evals/
│   ├── policies/
│   ├── providers/
│   └── ...
├── config/
│   └── security-policies.example.json
├── db/migrations/
├── docker/
│   └── entrypoint.sh
├── docs/
│   ├── docker.md
│   ├── prompt-injection-benchmark.md
│   ├── security-policy-profiles.md
│   ├── system-prompt-leakage.md
│   └── tool-authorization.md
├── evals/datasets/
│   └── prompt_injection_v1.json
├── tests/
├── .dockerignore
├── Dockerfile
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

- [x] versioned adversarial prompt dataset and measurable detection metrics
- [x] Dockerized gateway
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
