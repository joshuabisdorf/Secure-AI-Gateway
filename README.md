# Secure AI Gateway

Secure AI Gateway is a security-focused proxy that sits between applications and large language model providers or local model backends.

It provides a centralized enforcement point for client authentication, model access policy, provider routing, audit logging, request correlation, and future controls such as rate limits, usage budgets, PII handling, prompt-injection detection, and tool authorization.

## Project Status

Early development.

Currently implemented:

- FastAPI application
- `/health` endpoint
- OpenAI-style `/v1/chat/completions` endpoint
- provider abstraction
- deterministic non-network mock provider
- OpenAI upstream provider
- OpenRouter upstream provider
- environment-driven provider selection
- per-client gateway identities
- structured high-entropy gateway API keys
- hashed gateway API-key verification
- fail-closed model allowlist enforcement
- structured JSON audit logging
- client attribution in audit events
- request correlation through `X-Request-ID`
- requested-versus-resolved model audit attribution
- request latency measurement
- sanitized upstream provider failures
- automated tests with `pytest`

The next major milestone is per-client model policy, followed by rate limiting and usage budgets.

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
    +-- Authentication              [implemented]
    +-- Model allowlist             [implemented, global]
    +-- Audit logging               [implemented]
    +-- Request correlation         [implemented]
    +-- Provider routing            [implemented]
    +-- Per-client policy           [next]
    +-- Rate limiting               [planned]
    +-- Token and cost budgets      [planned]
    +-- PII detection/redaction     [planned]
    +-- Prompt-injection detection  [planned]
    +-- Tool permissions            [planned]
    |
    v
Configured model provider
```

Applications authenticate to the gateway instead of receiving direct access to upstream provider credentials.

## Configuration

Server configuration is supplied through environment variables. For local development:

```bash
cp .env.example .env
```

The server `.env` contains provider credentials and **hashed** gateway-client records. It does not need to contain raw gateway client keys.

### Gateway variables

| Variable | Purpose |
| --- | --- |
| `SAG_PROVIDER` | Selects the provider backend: `mock`, `openai`, or `openrouter`. |
| `SAG_CLIENTS` | Comma-separated hashed gateway client-key registry. |
| `SAG_ALLOWED_MODELS` | Comma-separated exact model names allowed by gateway policy. |

`SAG_CLIENTS` records use this format:

```text
client_id:key_id:sha256[,client_id:key_id:sha256...]
```

The raw API key is never stored in `SAG_CLIENTS`.

### Provider-specific variables

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required when `SAG_PROVIDER=openai`. |
| `OPENAI_BASE_URL` | Optional OpenAI API base URL override. |
| `OPENROUTER_API_KEY` | Required when `SAG_PROVIDER=openrouter`. |
| `OPENROUTER_BASE_URL` | Optional OpenRouter base URL override. Defaults to `https://openrouter.ai/api/v1`. |

`.env` is ignored by Git. Do not commit gateway client keys or upstream provider credentials.

## Gateway Client API Keys

Gateway client credentials have this structure:

```text
sag_<key_id>_<high-entropy-secret>
```

The `key_id` is public metadata used to locate a client record. The secret portion is randomly generated. The server stores a SHA-256 digest of the complete high-entropy key rather than the raw credential.

### Generate a client key

Generate a key for a client identity:

```bash
python -m app.api_keys local-dev
```

The command prints three values:

```text
Client ID: local-dev
API key: sag_<key-id>_<secret>
Server record: local-dev:<key-id>:<sha256>
```

The raw API key is shown so it can be delivered to the client. The server record contains only the one-way digest.

### Configure the server record

Copy the complete `Server record` value into `.env`:

```dotenv
SAG_CLIENTS=paste-server-record-here
```

For multiple clients, join records with commas:

```dotenv
SAG_CLIENTS=client-a:keya:hasha,client-b:keyb:hashb
```

Each `key_id` must be unique.

### Store the local client key separately

For local curl or SDK testing:

```bash
cp .client.env.example .client.env
```

Put the raw generated key in `.client.env`:

```dotenv
SAG_CLIENT_API_KEY=paste-raw-generated-key-here
```

`.client.env` is ignored by Git. This separation mirrors the real trust boundary: the gateway server keeps the hash; the client keeps the raw credential.

## Migration from `SAG_API_KEY`

The earlier single shared `SAG_API_KEY` configuration has been replaced by per-client identities.

Old server configuration:

```dotenv
SAG_API_KEY=shared-secret
```

New workflow:

1. Generate a client key with `python -m app.api_keys local-dev`.
2. Put the generated **Server record** in `SAG_CLIENTS` inside `.env`.
3. Put the generated raw **API key** in `.client.env` as `SAG_CLIENT_API_KEY`.
4. Restart the gateway.

This makes authentication attributable to a stable `client_id` and prevents the server-side client registry from storing raw gateway bearer secrets.

## Providers

### Mock provider

The **mock provider** is deterministic and non-production. It performs no external network request and is used for local development and automated tests.

```dotenv
SAG_PROVIDER=mock
SAG_ALLOWED_MODELS=mock-model
```

### OpenAI provider

The OpenAI provider forwards approved requests to the OpenAI API.

```dotenv
SAG_PROVIDER=openai
SAG_ALLOWED_MODELS=your-model-name
OPENAI_API_KEY=your-provider-key
OPENAI_BASE_URL=
```

Real upstream requests may incur provider charges.

### OpenRouter provider

The OpenRouter provider forwards approved requests through OpenRouter's OpenAI-compatible chat-completions API.

For free-model experimentation:

```dotenv
SAG_PROVIDER=openrouter
SAG_ALLOWED_MODELS=openrouter/free
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

`OPENROUTER_BASE_URL` can normally remain empty; the gateway defaults it to:

```text
https://openrouter.ai/api/v1
```

`openrouter/free` is a routing alias. OpenRouter may resolve it to a different concrete free model on each request. The gateway records both the requested alias and the model reported by the upstream response.

## Security Behavior

Before a chat-completion request reaches a provider, the gateway requires:

1. A valid structured gateway API key.
2. A matching hashed client-key record in `SAG_CLIENTS`.
3. A requested model that appears in `SAG_ALLOWED_MODELS`.

The gateway fails closed:

- missing or invalid client registry returns `503`
- missing or invalid client credentials return `401`
- missing model policy returns `503`
- disallowed model requests return `403`
- known upstream provider failures are converted to a generic `502`

The current model allowlist is global. Per-client model policy is the next security milestone.

## Request Correlation

Each request receives a correlation identifier. A valid caller-supplied `X-Request-ID` is preserved; otherwise the gateway generates one.

The identifier is returned in the `X-Request-ID` response header and included in audit records.

## Audit Logging

Security-relevant decisions are emitted as structured JSON through the `secure_ai_gateway.audit` logger.

Successful authenticated events can include:

```json
{
  "event": "chat_completion",
  "outcome": "success",
  "client_id": "local-dev",
  "key_id": "abcd1234",
  "provider": "openrouter",
  "requested_model": "openrouter/free",
  "resolved_model": "provider/model:free",
  "request_id": "req_...",
  "latency_ms": 123.456
}
```

The audit layer intentionally does not log:

- raw gateway API keys
- prompt or message content
- upstream provider credentials
- upstream provider response bodies

Authentication failures record sanitized reason codes rather than credentials. Successful authentication records `client_id` and the non-secret `key_id`, allowing later rate limits, budgets, and policies to be attributed to a specific client.

## Local Development

### Requirements

- Python 3.13+
- Git

### Clone and install

```bash
git clone git@github.com:joshuabisdorf/Secure-AI-Gateway.git
cd Secure-AI-Gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### Configure the server

```bash
cp .env.example .env
python -m app.api_keys local-dev
```

Copy the generated server record into `SAG_CLIENTS`, configure the provider and model allowlist, then start:

```bash
uvicorn app.main:app --reload --env-file .env
```

### Configure the local client

```bash
cp .client.env.example .client.env
```

Put the generated raw key in `SAG_CLIENT_API_KEY`, then load it in the client terminal:

```bash
set -a
source .client.env
set +a
```

### Health check

```bash
curl -i http://127.0.0.1:8000/health
```

### Chat request

For an OpenRouter free-model configuration:

```bash
curl -i \
  -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $SAG_CLIENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/free",
    "messages": [
      {"role": "user", "content": "Reply with exactly: Secure gateway works"}
    ]
  }'
```

FastAPI documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## Testing

Run:

```bash
pytest -q
```

The test suite forces the mock provider so local provider settings cannot accidentally cause billable upstream calls. Provider integrations use mocked HTTP transport. Authentication tests use a deterministic raw test key whose SHA-256 digest is stored in the test client registry.

## Repository Layout

```text
Secure-AI-Gateway/
├── app/
│   ├── api_keys.py
│   ├── audit.py
│   ├── auth.py
│   ├── main.py
│   ├── models.py
│   ├── policies/
│   │   └── model_access.py
│   └── providers/
│       ├── base.py
│       ├── factory.py
│       ├── mock.py
│       ├── openai.py
│       └── openrouter.py
├── tests/
│   ├── conftest.py
│   ├── test_api_keys.py
│   ├── test_audit.py
│   ├── test_auth.py
│   ├── test_chat.py
│   ├── test_health.py
│   ├── test_model_policy.py
│   ├── test_openai_provider.py
│   └── test_openrouter_provider.py
├── .client.env.example
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Development Across Environments

The project can be developed from separate Linux working copies, including native Linux and WSL2. GitHub is the synchronization point for source code.

Do not share environment-specific state between working copies:

- `.venv`
- `.env`
- `.client.env`
- secrets
- local databases
- Docker containers and volumes

Typical workflow:

```bash
git pull
source .venv/bin/activate
pytest -q
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
- [x] model allowlist enforcement
- [x] structured audit logging
- [x] request correlation
- [ ] per-client model policy
- [ ] persistent client/key registry
- [ ] key revocation and rotation workflow
- [ ] rate limiting
- [ ] token and cost budgets

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
- [ ] Redis
- [ ] GitHub Actions CI
- [ ] OpenTelemetry / Prometheus
- [ ] Kubernetes
- [ ] Terraform
- [ ] cloud deployment

## Testing Philosophy

Security features should be measurable rather than assumed. External provider calls remain behind provider interfaces so most tests can run deterministically without network access, provider credentials, or API cost.

## License

No license has been selected yet.
