# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and large language model providers or local model backends.

It provides a centralized enforcement point for client authentication, model authorization, request throttling, usage budgets, provider routing, audit logging, request correlation, and future controls such as PII handling, prompt-injection detection, and tool authorization.

## Project Status

Early development.

Currently implemented:

- FastAPI application
- `/health` endpoint
- OpenAI-style `/v1/chat/completions` endpoint
- deterministic non-network mock provider
- OpenAI upstream provider
- OpenRouter upstream provider
- per-client identities and structured gateway API keys
- hashed gateway API-key verification
- deployment-wide and per-client model allowlists
- per-client requests-per-minute rate limiting
- per-client daily token and cost budgets
- provider usage normalization
- structured JSON audit logging with client attribution
- request correlation through `X-Request-ID`
- requested-versus-resolved model attribution
- sanitized upstream provider failures
- automated tests with `pytest`

The next infrastructure milestone is persistent policy and usage state so client records, revocation, rate limits, and budgets can survive process restarts and scale across replicas.

## Architecture

```text
Application / Client
    |
    | sag_<key_id>_<secret>
    v
Secure AI Gateway
    |
    +-- Client identity             [implemented]
    +-- Hashed API-key verification [implemented]
    +-- Per-client rate limiting    [implemented]
    +-- Global model allowlist      [implemented]
    +-- Per-client model policy     [implemented]
    +-- Daily token/cost budgets    [implemented]
    +-- Audit logging               [implemented]
    +-- Request correlation         [implemented]
    +-- Provider routing            [implemented]
    +-- Persistent policy state     [next]
    +-- PII detection/redaction     [planned]
    +-- Prompt-injection detection  [planned]
    +-- Tool permissions            [planned]
    |
    v
Configured model provider
```

Applications authenticate to the gateway instead of receiving direct access to upstream provider credentials.

## Configuration

For local development:

```bash
cp .env.example .env
```

The server `.env` contains provider credentials, hashed gateway-client records, authorization policy, rate limits, and usage budgets. Raw gateway client keys stay outside the server configuration.

### Gateway variables

| Variable | Purpose |
| --- | --- |
| `SAG_PROVIDER` | Provider backend: `mock`, `openai`, or `openrouter`. |
| `SAG_CLIENTS` | Comma-separated hashed gateway client-key registry. |
| `SAG_ALLOWED_MODELS` | Deployment-wide model ceiling. |
| `SAG_CLIENT_ALLOWED_MODELS` | Per-client exact model grants. |
| `SAG_CLIENT_RATE_LIMITS` | Per-client protected chat requests allowed per minute. |
| `SAG_CLIENT_DAILY_BUDGETS` | Per-client UTC-day token and USD budgets. |

Client records use:

```text
client_id:key_id:sha256[,client_id:key_id:sha256...]
```

Model grants use:

```text
client_id:model[,client_id:model...]
```

Rate limits use:

```text
client_id:requests_per_minute[,client_id:requests_per_minute...]
```

Daily usage budgets use:

```text
client_id:daily_tokens:daily_cost_usd[,client_id:daily_tokens:daily_cost_usd...]
```

Use `-` to disable one budget dimension:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00,service-a:100000:-,service-b:-:5.00
```

This means:

- `local-dev` may consume up to 50,000 tokens and $1.00 per UTC day.
- `service-a` has a 100,000-token budget with no gateway cost ceiling.
- `service-b` has a $5.00 cost budget with no gateway token ceiling.

At least one dimension must be enabled for every configured client.

### Provider-specific variables

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required when `SAG_PROVIDER=openai`. |
| `OPENAI_BASE_URL` | Optional OpenAI API base URL override. |
| `OPENROUTER_API_KEY` | Required when `SAG_PROVIDER=openrouter`. |
| `OPENROUTER_BASE_URL` | Optional OpenRouter base URL override. Defaults to `https://openrouter.ai/api/v1`. |

`.env` and `.client.env` are ignored by Git. Do not commit gateway client keys or upstream provider credentials.

## Gateway Client API Keys

Gateway client credentials have this structure:

```text
sag_<key_id>_<high-entropy-secret>
```

Generate one with:

```bash
python -m app.api_keys local-dev
```

The command prints:

```text
Client ID: local-dev
API key: sag_<key-id>_<secret>
Server record: local-dev:<key-id>:<sha256>
```

Put only the server record in `.env`:

```dotenv
SAG_CLIENTS=paste-server-record-here
```

Keep the raw client key separately:

```bash
cp .client.env.example .client.env
```

```dotenv
SAG_CLIENT_API_KEY=paste-raw-generated-key-here
```

## Per-Client Rate Limiting

`SAG_CLIENT_RATE_LIMITS` defines a fixed requests-per-minute limit for each authenticated client:

```dotenv
SAG_CLIENT_RATE_LIMITS=local-dev:10,service-a:60
```

The limiter runs after authentication and before model/provider work. Every authenticated chat attempt consumes client capacity, including requests later denied by model policy.

Successful responses include:

```text
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9
```

An exhausted rate window returns `429 Too Many Requests` with `Retry-After`.

The current rate limiter is intentionally process-local and in memory. Redis-backed distributed enforcement is planned before multi-worker or multi-replica deployment.

## Per-Client Daily Usage Budgets

`SAG_CLIENT_DAILY_BUDGETS` limits cumulative provider-reported usage for each UTC day:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00
```

The gateway checks accumulated usage before forwarding. Exact usage is only known after a provider completes a request, so the request that crosses the remaining budget is returned and accounted. Subsequent requests are blocked with:

```text
HTTP/1.1 403 Forbidden
X-Usage-Budget-Reset: <next-UTC-midnight>
```

```json
{"detail":"Usage budget exceeded."}
```

Successful responses expose cumulative daily accounting:

```text
X-Usage-Tokens-Used: 123
X-Usage-Tokens-Remaining: 49877
X-Usage-Cost-USD: 0.00042
X-Usage-Cost-Remaining-USD: 0.99958
X-Usage-Budget-Reset: 2026-09-10T00:00:00+00:00
```

The usage ledger is currently process-local and in memory, so restarting the process resets it. Persistent accounting is required before these budgets are production-grade or shared across replicas.

### Provider accounting behavior

The normalized gateway response supports:

```json
{
  "usage": {
    "prompt_tokens": 7,
    "completion_tokens": 5,
    "total_tokens": 12,
    "cost": 0.00042
  }
}
```

OpenRouter is configured to request its usage accounting, which includes token counts and actual request cost. Free models normally report zero cost while still consuming tokens.

Direct OpenAI Chat Completions provides token usage but not a provider-reported dollar-cost field. Therefore direct OpenAI can currently use a token-only budget such as:

```dotenv
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-
```

Provider-independent cost enforcement will require a trusted pricing layer that maps resolved models and usage to current prices.

If a configured budget depends on accounting data that the provider does not return, the gateway fails closed with a sanitized `502` instead of silently bypassing the budget.

## Providers

### Mock provider

The mock provider is deterministic and non-production. It performs no external network request and reports deterministic usage for tests.

```dotenv
SAG_PROVIDER=mock
SAG_ALLOWED_MODELS=mock-model
SAG_CLIENT_ALLOWED_MODELS=local-dev:mock-model
SAG_CLIENT_RATE_LIMITS=local-dev:60
SAG_CLIENT_DAILY_BUDGETS=local-dev:10000:10.00
```

### OpenAI provider

```dotenv
SAG_PROVIDER=openai
SAG_ALLOWED_MODELS=your-model-name
SAG_CLIENT_ALLOWED_MODELS=local-dev:your-model-name
SAG_CLIENT_RATE_LIMITS=local-dev:30
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:-
OPENAI_API_KEY=your-provider-key
OPENAI_BASE_URL=
```

Real upstream requests may incur provider charges.

### OpenRouter provider

For free-model experimentation:

```dotenv
SAG_PROVIDER=openrouter
SAG_ALLOWED_MODELS=openrouter/free
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free
SAG_CLIENT_RATE_LIMITS=local-dev:10
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

`OPENROUTER_BASE_URL` can normally remain empty. `openrouter/free` is a routing alias, so OpenRouter may resolve it to a different concrete free model on each request. The gateway records both the requested alias and resolved model.

## Security Behavior

Before a chat-completion request reaches a provider, the gateway requires:

1. a valid structured gateway API key;
2. a matching hashed client-key record;
3. configured per-client rate policy with available capacity;
4. a requested model present in the global allowlist;
5. a matching per-client model grant; and
6. configured daily usage policy with remaining accumulated capacity.

The gateway fails closed:

- invalid or missing client registry: `503`
- missing or invalid client credentials: `401`
- missing or invalid rate policy: `503`
- exceeded request rate: `429`
- missing model policy: `503`
- denied model: `403`
- missing or invalid usage-budget policy: `503`
- exhausted daily usage budget: `403`
- required provider accounting unavailable: `502`
- known upstream provider failures: generic `502`

## Request Correlation and Audit Logging

Each request receives an `X-Request-ID`. A valid caller-supplied value is preserved; otherwise the gateway generates one.

Security-relevant decisions are emitted as one-line JSON records to application stderr, so they appear directly in the terminal running Uvicorn. The audit layer includes safe metadata such as `client_id`, `key_id`, model names, rate-limit state, cumulative token usage, cumulative cost, and budget reset time.

A usage accounting event can look like:

```json
{
  "event": "usage_budget",
  "outcome": "recorded",
  "client_id": "local-dev",
  "provider": "openrouter",
  "request_tokens": 12,
  "request_cost_usd": 0.00042,
  "tokens_used_daily": 125,
  "tokens_remaining_daily": 49875,
  "cost_used_daily_usd": 0.00042,
  "cost_remaining_daily_usd": 0.99958,
  "request_id": "req_..."
}
```

The audit layer intentionally does not log raw gateway API keys, prompt/message content, upstream provider credentials, or upstream response bodies.

## Local Development

### Requirements

- Python 3.13+
- Git

### Install

```bash
git clone git@github.com:joshuabisdorf/Secure-AI-Gateway.git
cd Secure-AI-Gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### Current OpenRouter development configuration

After generating a `local-dev` client record, a typical `.env` is:

```dotenv
SAG_PROVIDER=openrouter
SAG_CLIENTS=your-existing-server-record
SAG_ALLOWED_MODELS=openrouter/free
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free
SAG_CLIENT_RATE_LIMITS=local-dev:10
SAG_CLIENT_DAILY_BUDGETS=local-dev:50000:1.00
OPENROUTER_API_KEY=your-existing-openrouter-key
OPENROUTER_BASE_URL=
```

Start the gateway:

```bash
uvicorn app.main:app --reload --env-file .env
```

Load the client key in another terminal:

```bash
set -a
source .client.env
set +a
```

Then call:

```bash
curl -i \
  -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $SAG_CLIENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/free",
    "messages": [
      {"role": "user", "content": "Reply with exactly: Usage accounting works"}
    ]
  }'
```

FastAPI documentation is available at `http://127.0.0.1:8000/docs`.

## Testing

Run:

```bash
pytest -q
```

The test suite forces the mock provider so local provider settings cannot accidentally cause billable upstream calls. Provider integrations use mocked HTTP transport. Process-local rate-limit and usage-budget state is reset between tests.

## Repository Layout

```text
Secure-AI-Gateway/
├── app/
│   ├── api_keys.py
│   ├── audit.py
│   ├── auth.py
│   ├── main.py
│   ├── models.py
│   ├── rate_limit.py
│   ├── usage_budget.py
│   ├── policies/
│   │   └── model_access.py
│   └── providers/
│       ├── base.py
│       ├── factory.py
│       ├── mock.py
│       ├── openai.py
│       └── openrouter.py
├── tests/
│   ├── test_rate_limit.py
│   └── test_usage_budget.py
├── .client.env.example
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Function Documentation Convention

Project functions use an RME-style docstring where appropriate:

- **Requires** — conditions that must be true before the function runs
- **Modifies** — state or resources changed by the function
- **Effects** — externally visible actions or side effects
- **Inputs** — function inputs
- **Outputs** — returned values or produced outputs

## Roadmap

### Phase 1 — Gateway foundation

- [x] FastAPI application
- [x] health endpoint
- [x] automated test foundation
- [x] OpenAI-compatible chat-completions schema
- [x] provider interface
- [x] deterministic mock provider
- [x] OpenAI upstream provider
- [x] OpenRouter upstream provider
- [x] provider selection

### Phase 2 — Core security controls

- [x] per-client identities
- [x] structured gateway API keys
- [x] hashed API-key verification
- [x] deployment-wide model allowlist
- [x] per-client model allowlists
- [x] per-client rate limiting
- [x] daily token and cost budgets
- [x] structured audit logging
- [x] request correlation
- [ ] persistent client/key registry
- [ ] key revocation and rotation workflow
- [ ] persistent/distributed usage accounting

### Phase 3 — LLM security controls

- [ ] PII detection and redaction
- [ ] prompt-injection detection
- [ ] system-prompt leakage tests
- [ ] tool authorization
- [ ] configurable security policies

### Phase 4 — Evaluation

- [ ] adversarial prompt dataset
- [ ] attack detection rate measurement
- [ ] false-positive rate measurement
- [ ] latency overhead measurement
- [ ] cost overhead measurement
- [ ] provider/model comparisons

### Phase 5 — Infrastructure

- [ ] Dockerized gateway
- [ ] PostgreSQL
- [ ] Redis-backed distributed rate limiting
- [ ] GitHub Actions CI
- [ ] OpenTelemetry / Prometheus
- [ ] Kubernetes
- [ ] Terraform
- [ ] cloud deployment

## Testing Philosophy

Security features should be measurable rather than assumed. External provider calls remain behind provider interfaces so most tests can run deterministically without network access, provider credentials, or API cost.

## License

No license has been selected yet.
