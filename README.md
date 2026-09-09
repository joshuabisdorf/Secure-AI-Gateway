# Secure AI Gateway

Secure AI Gateway is a security-focused proxy that sits between applications and large language model providers or local model backends.

It provides a centralized enforcement point for authentication, model access policy, provider routing, audit logging, request correlation, and future controls such as rate limits, usage budgets, PII handling, prompt-injection detection, and tool authorization.

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
- bearer API-key authentication
- fail-closed model allowlist enforcement
- structured JSON audit logging
- requested-versus-resolved model audit attribution
- request correlation through `X-Request-ID`
- request latency measurement
- sanitized upstream provider failures
- automated tests with `pytest`

The next major milestone is stronger caller identity and policy controls, followed by rate limiting and usage budgets.

## Architecture

```text
Application
    |
    | Gateway bearer API key
    v
Secure AI Gateway
    |
    +-- Authentication              [implemented]
    +-- Model allowlist             [implemented]
    +-- Audit logging               [implemented]
    +-- Request correlation         [implemented]
    +-- Provider routing            [implemented]
    +-- Rate limiting               [planned]
    +-- Token and cost budgets      [planned]
    +-- PII detection/redaction     [planned]
    +-- Prompt-injection detection  [planned]
    +-- Tool permissions            [planned]
    +-- Policy engine               [planned]
    |
    v
Configured model provider
```

Applications authenticate to the gateway instead of receiving direct access to upstream provider credentials.

## Configuration

Configuration is supplied through environment variables. For local development, copy the committed template and keep the real `.env` file private:

```bash
cp .env.example .env
```

Then edit `.env` for the environment you are running. The committed template uses shell-safe assignments, so it can also be loaded into a Bash session when needed.

### Gateway variables

| Variable | Purpose |
| --- | --- |
| `SAG_PROVIDER` | Selects the provider backend. Currently `mock`, `openai`, or `openrouter`. |
| `SAG_API_KEY` | Bearer credential required by clients calling the gateway. |
| `SAG_ALLOWED_MODELS` | Comma-separated list of exact model names allowed by gateway policy. |

### Provider-specific variables

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required when `SAG_PROVIDER=openai`. |
| `OPENAI_BASE_URL` | Optional OpenAI API base URL override. |
| `OPENROUTER_API_KEY` | Required when `SAG_PROVIDER=openrouter`. |
| `OPENROUTER_BASE_URL` | Optional OpenRouter API base URL override. Defaults to `https://openrouter.ai/api/v1`. |

`.env` is ignored by Git. Do not commit real gateway keys or upstream provider credentials.

### Loading `.env`

Uvicorn can load the file directly:

```bash
uvicorn app.main:app --reload --env-file .env
```

If another shell also needs the values, for example to use `$SAG_API_KEY` in `curl`, load them with:

```bash
set -a
source .env
set +a
```

## Providers

### Mock provider

The **mock provider** is deterministic and non-production. It performs no external network request and is used for local development and automated tests.

```dotenv
SAG_PROVIDER=mock
SAG_API_KEY=local-gateway-key
SAG_ALLOWED_MODELS=mock-model
```

### OpenAI provider

The OpenAI provider forwards approved requests to the OpenAI API.

```dotenv
SAG_PROVIDER=openai
SAG_API_KEY=local-gateway-key
SAG_ALLOWED_MODELS=your-model-name
OPENAI_API_KEY=your-provider-key
OPENAI_BASE_URL=
```

Real upstream requests may incur provider charges.

### OpenRouter provider

The OpenRouter provider forwards approved requests through OpenRouter's OpenAI-compatible chat-completions API.

Create an OpenRouter API key from your account, then configure the gateway:

```dotenv
SAG_PROVIDER=openrouter
SAG_API_KEY=local-gateway-key
SAG_ALLOWED_MODELS=openrouter/free
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_BASE_URL=
```

`OPENROUTER_BASE_URL` can normally remain empty; the gateway defaults it to:

```text
https://openrouter.ai/api/v1
```

For no-token-cost experimentation, `openrouter/free` lets OpenRouter choose from currently available free models. You may also allow specific OpenRouter model slugs instead. Free-model availability and rate limits are controlled by OpenRouter and can change independently of this project.

Example gateway request:

```bash
curl -i \
  -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $SAG_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/free",
    "messages": [
      {"role": "user", "content": "Reply with exactly: Secure gateway works"}
    ]
  }'
```

Router aliases can resolve to a different concrete upstream model. The gateway therefore audits both the model requested by the client and the model reported by the provider response.

## Security Behavior

Before a chat-completion request reaches a provider, the gateway requires:

1. A valid gateway bearer API key.
2. A requested model that appears in `SAG_ALLOWED_MODELS`.

The model policy fails closed:

- missing or unusable model policy returns `503`
- disallowed model requests return `403`
- missing or invalid gateway credentials return `401`
- known upstream provider failures are converted to a generic `502`

Provider credentials and provider response bodies are not returned in gateway errors.

## Request Correlation

Each request receives a correlation identifier. A valid caller-supplied `X-Request-ID` is preserved; otherwise, the gateway generates one.

The identifier is returned in the `X-Request-ID` response header and included in audit records.

## Audit Logging

Security-relevant decisions are emitted as structured JSON through the `secure_ai_gateway.audit` logger.

Example events:

```json
{"event":"authentication","outcome":"allow","request_id":"req_...","timestamp":"..."}
{"event":"model_policy","outcome":"allow","request_id":"req_...","requested_model":"openrouter/free","timestamp":"..."}
{"event":"chat_completion","latency_ms":123.456,"outcome":"success","provider":"openrouter","request_id":"req_...","requested_model":"openrouter/free","resolved_model":"provider/model:free","timestamp":"..."}
```

`requested_model` is the model or router alias evaluated by gateway policy. `resolved_model` is the concrete model name reported by the successful upstream response. They may be identical for direct model requests and different when a provider performs routing.

The audit layer intentionally does not log:

- prompt or message content
- gateway bearer API keys
- upstream provider credentials
- upstream provider response bodies

Authentication failures record non-secret reason codes such as `missing_key`, `invalid_key`, or `not_configured`. Provider failures use sanitized reason codes such as `upstream_timeout`, `upstream_network_error`, or `upstream_http_429`.

## Repository Layout

```text
Secure-AI-Gateway/
├── app/
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
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Development Setup

### Requirements

- Python 3.13+
- Git

Docker is installed for future milestones but is not required to run the current application.

### Clone and install

```bash
git clone git@github.com:joshuabisdorf/Secure-AI-Gateway.git
cd Secure-AI-Gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### Configure the environment

```bash
cp .env.example .env
```

Edit `.env`, then start the gateway with:

```bash
uvicorn app.main:app --reload --env-file .env
```

### Run tests

```bash
pytest -q
```

The test suite forces the mock provider so local environment settings cannot accidentally cause billable provider calls during normal tests. Provider integrations use mocked HTTP transport for deterministic tests.

### Health check

```bash
curl -i http://127.0.0.1:8000/health
```

Expected body:

```json
{"status":"ok"}
```

FastAPI documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## Development Across Environments

The project can be developed from separate Linux working copies, including native Linux and WSL2. GitHub is the synchronization point for source code.

Do not share environment-specific state between working copies:

- `.venv`
- `.env`
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

- [x] API-key authentication
- [x] model allowlist enforcement
- [x] structured audit logging
- [x] request correlation
- [ ] per-user identities and API keys
- [ ] hashed API-key storage
- [ ] policy engine
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
