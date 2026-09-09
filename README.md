# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and large language model providers or local model backends.

It provides a centralized enforcement point for client authentication, model authorization, provider routing, audit logging, request correlation, and future controls such as rate limits, usage budgets, PII handling, prompt-injection detection, and tool authorization.

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
- per-client gateway identities
- structured high-entropy gateway API keys
- hashed gateway API-key verification
- deployment-wide model allowlist
- per-client model allowlists
- structured JSON audit logging with client attribution
- request correlation through `X-Request-ID`
- requested-versus-resolved model audit attribution
- request latency measurement
- sanitized upstream provider failures
- automated tests with `pytest`

The next major milestone is per-client rate limiting, followed by token and cost budgets.

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
    +-- Global model allowlist      [implemented]
    +-- Per-client model policy     [implemented]
    +-- Audit logging               [implemented]
    +-- Request correlation         [implemented]
    +-- Provider routing            [implemented]
    +-- Per-client rate limiting    [next]
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

For local development:

```bash
cp .env.example .env
```

The server `.env` contains provider credentials, hashed gateway-client records, and authorization policy. It does not need to contain raw gateway client keys.

### Gateway variables

| Variable | Purpose |
| --- | --- |
| `SAG_PROVIDER` | Provider backend: `mock`, `openai`, or `openrouter`. |
| `SAG_CLIENTS` | Comma-separated hashed gateway client-key registry. |
| `SAG_ALLOWED_MODELS` | Deployment-wide model ceiling. |
| `SAG_CLIENT_ALLOWED_MODELS` | Per-client model grants. |

`SAG_CLIENTS` records use:

```text
client_id:key_id:sha256[,client_id:key_id:sha256...]
```

Per-client model grants use:

```text
client_id:model[,client_id:model...]
```

Repeat a client ID to grant multiple models:

```dotenv
SAG_ALLOWED_MODELS=openrouter/free,nvidia/model:free
SAG_CLIENT_ALLOWED_MODELS=local-dev:openrouter/free,service-a:nvidia/model:free
```

A request is forwarded only when the requested model passes **both** policy layers:

1. the model is globally enabled in `SAG_ALLOWED_MODELS`; and
2. the authenticated `client_id` has that exact model in `SAG_CLIENT_ALLOWED_MODELS`.

This lets the global allowlist act as a hard deployment ceiling while individual clients receive narrower permissions.

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

The server stores a SHA-256 digest of the complete high-entropy key rather than the raw credential.

Generate a key for a client identity:

```bash
python -m app.api_keys local-dev
```

The command prints:

```text
Client ID: local-dev
API key: sag_<key-id>_<secret>
Server record: local-dev:<key-id>:<sha256>
```

Put the server record in `.env`:

```dotenv
SAG_CLIENTS=paste-server-record-here
```

Store the raw key separately for the client:

```bash
cp .client.env.example .client.env
```

```dotenv
SAG_CLIENT_API_KEY=paste-raw-generated-key-here
```

Both `.env` and `.client.env` are ignored by Git.

## Providers

### Mock provider

The mock provider is deterministic and non-production. It performs no external network request.

```dotenv
SAG_PROVIDER=mock
SAG_ALLOWED_MODELS=mock-model
SAG_CLIENT_ALLOWED_MODELS=local-dev:mock-model
```

### OpenAI provider

```dotenv
SAG_PROVIDER=openai
SAG_ALLOWED_MODELS=your-model-name
SAG_CLIENT_ALLOWED_MODELS=local-dev:your-model-name
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
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

`OPENROUTER_BASE_URL` can normally remain empty. `openrouter/free` is a routing alias, so OpenRouter may resolve it to a different concrete free model on each request. The gateway records both the requested alias and the model reported by the upstream response.

## Security Behavior

Before a chat-completion request reaches a provider, the gateway requires:

1. a valid structured gateway API key;
2. a matching hashed client-key record in `SAG_CLIENTS`;
3. a requested model present in the global allowlist; and
4. a matching model grant for the authenticated client.

The gateway fails closed:

- missing or invalid client registry returns `503`
- missing or invalid client credentials return `401`
- missing global model policy returns `503`
- missing or invalid per-client model policy returns `503`
- models denied by either authorization layer return `403`
- known upstream provider failures are converted to a generic `502`

The same generic `403` response is used for global and client-level model denials so the API does not disclose internal policy structure.

## Request Correlation and Audit Logging

Each request receives an `X-Request-ID`. A valid caller-supplied value is preserved; otherwise the gateway generates one.

Security-relevant decisions are emitted as structured JSON through the `secure_ai_gateway.audit` logger. Successful completion events can include:

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

The audit layer intentionally does not log raw gateway API keys, prompt/message content, upstream provider credentials, or upstream response bodies.

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

Copy the generated server record into `SAG_CLIENTS`, configure provider credentials, set the global allowlist, and grant the desired model to `local-dev` in `SAG_CLIENT_ALLOWED_MODELS`.

Start the gateway:

```bash
uvicorn app.main:app --reload --env-file .env
```

### Configure the local client

```bash
cp .client.env.example .client.env
```

Put the generated raw key in `SAG_CLIENT_API_KEY`, then load it:

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

For OpenRouter free routing:

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

FastAPI documentation is available at `http://127.0.0.1:8000/docs`.

## Testing

Run:

```bash
pytest -q
```

The test suite forces the mock provider so local provider settings cannot accidentally cause billable upstream calls. Provider integrations use mocked HTTP transport.

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
- [x] structured audit logging
- [x] request correlation
- [ ] persistent client/key registry
- [ ] key revocation and rotation workflow
- [ ] per-client rate limiting
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
