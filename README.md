# Secure AI Gateway

A security-focused gateway that sits between applications and large language model providers or local models.

The project provides a centralized enforcement point for authentication, authorization, usage controls, data protection, model access, tool permissions, and security telemetry before requests reach an LLM provider.

## Project Status

Early development.

The current codebase provides:

- FastAPI application foundation
- `/health` endpoint
- OpenAI-style `/v1/chat/completions` endpoint
- provider abstraction with a deterministic fake provider
- bearer API-key authentication
- fail-closed model allowlist enforcement
- structured JSON audit logging
- request correlation through `X-Request-ID`
- request latency measurement
- automated tests with `pytest`
- isolated Python project configuration

The next milestone is a real upstream provider implementation, followed by stronger identity and policy controls.

## Architecture

```text
Application
    |
    | Bearer gateway API key
    v
Secure AI Gateway
    |
    +-- Authentication              [implemented]
    +-- Model allowlist             [implemented]
    +-- Audit logging               [implemented]
    +-- Request correlation         [implemented]
    +-- Rate limiting               [planned]
    +-- Token and cost budgets      [planned]
    +-- PII detection/redaction     [planned]
    +-- Prompt-injection detection  [planned]
    +-- Tool permissions            [planned]
    +-- Policy engine               [planned]
    +-- Model routing               [planned]
    |
    v
OpenAI / Anthropic / Local Models
```

Applications authenticate to the gateway rather than receiving direct access to upstream provider credentials.

## Current Security Behavior

The chat-completions endpoint requires two independent conditions before a request reaches the provider:

1. The caller must provide the configured `SAG_API_KEY` as a bearer token.
2. The requested model must appear in `SAG_ALLOWED_MODELS`.

The model policy fails closed. If no usable allowlist is configured, authenticated requests return `503` rather than being forwarded without policy enforcement. Requests for models outside the allowlist return `403`.

Each request also receives a correlation identifier. A valid caller-supplied `X-Request-ID` is preserved; otherwise the gateway generates an identifier such as:

```text
req_4f7d8e0bd18e4fcbb56ef2577b141e03
```

The identifier is returned in the `X-Request-ID` response header and attached to audit records.

Example environment configuration:

```bash
export SAG_API_KEY="sag_dev_change_me"
export SAG_ALLOWED_MODELS="fake-model,another-model"
```

Do not commit real API keys. `.env` is ignored by Git; `.env.example` documents supported development variables without containing secrets.

## Audit Logging

Security-relevant decisions are emitted as structured JSON through the `secure_ai_gateway.audit` logger.

Examples:

```json
{"event":"authentication","outcome":"allow","request_id":"req_...","timestamp":"..."}
{"event":"model_policy","model":"fake-model","outcome":"allow","request_id":"req_...","timestamp":"..."}
{"event":"chat_completion","latency_ms":1.234,"model":"fake-model","outcome":"success","request_id":"req_...","timestamp":"..."}
```

The audit layer intentionally does **not** log:

- prompt or message content
- bearer API keys
- upstream provider credentials

Authentication failures record non-secret reasons such as `missing_key`, `invalid_key`, or `not_configured`. Model-policy denials record the requested model and policy result without recording prompt content.

## Planned Security Controls

- per-user API keys and identities
- hashed API-key storage
- per-user rate limits
- per-request and per-user token/cost budgets
- persistent and queryable audit storage
- PII detection and configurable redaction/blocking
- prompt-injection detection
- system-prompt leakage testing
- configurable security policies
- explicit tool authorization
- least-privilege enforcement for agent actions
- adversarial security testing

A future user policy may look like:

```yaml
user: alice
allowed_tools:
  - weather
  - search

denied_tools:
  - shell
  - filesystem

max_cost_per_request: 0.10
```

LLM output is treated as untrusted input. A model may request an action, but authorization is enforced by code outside the model.

## Technology Stack

Current:

- Python 3.13+
- FastAPI
- Pydantic
- httpx
- Uvicorn
- pytest

Planned:

- PostgreSQL
- Redis
- Docker / Docker Compose
- Kubernetes
- Terraform
- OpenTelemetry
- Prometheus
- GitHub Actions
- TypeScript dashboard
- AWS, GCP, or Azure deployment

## Repository Layout

```text
Secure-AI-Gateway/
├── app/
│   ├── __init__.py
│   ├── audit.py
│   ├── auth.py
│   ├── main.py
│   ├── models.py
│   ├── policies/
│   │   ├── __init__.py
│   │   └── model_access.py
│   └── providers/
│       ├── __init__.py
│       ├── base.py
│       └── fake.py
├── tests/
│   ├── test_audit.py
│   ├── test_auth.py
│   ├── test_chat.py
│   ├── test_health.py
│   └── test_model_policy.py
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

### Clone the repository

```bash
git clone git@github.com:joshuabisdorf/Secure-AI-Gateway.git
cd Secure-AI-Gateway
```

### Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### Configure development security policy

```bash
export SAG_API_KEY="sag_dev_change_me"
export SAG_ALLOWED_MODELS="fake-model"
```

### Run the tests

```bash
pytest -q
```

### Run the gateway

```bash
uvicorn app.main:app --reload
```

Health check:

```bash
curl -i http://127.0.0.1:8000/health
```

Expected body:

```json
{"status":"ok"}
```

The response also contains an `X-Request-ID` header.

Authenticated chat request:

```bash
curl -i \
  -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $SAG_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "fake-model",
    "messages": [{"role": "user", "content": "hello"}]
  }'
```

FastAPI documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## Development Across LMDE and WSL2

The project uses separate Linux working copies on LMDE and Ubuntu under WSL2.

```text
             GitHub
            /      \
           /        \
        LMDE        WSL2 Ubuntu
          |             |
      local .venv   local .venv
      Docker Engine Docker Desktop
```

GitHub is the synchronization point for source code. Virtual environments, `.env` files, secrets, local databases, and Docker containers are not shared between environments.

Before starting work:

```bash
git pull
source .venv/bin/activate
```

After completing a tested local change:

```bash
pytest -q
git status
git add .
git commit -m "Describe the change"
git push
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
- [x] fake provider
- [ ] real upstream provider

### Phase 2 — Core security controls

- [x] API-key authentication
- [x] model allowlist enforcement
- [x] structured audit logging
- [x] request correlation
- [ ] per-user identities and API keys
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

Security features should be measurable rather than assumed.

The project will include both benign and adversarial inputs so controls can be evaluated using metrics such as:

```text
Detection Rate      = TP / (TP + FN)
False Positive Rate = FP / (FP + TN)
Precision           = TP / (TP + FP)
Latency Overhead    = gateway latency - direct provider latency
```

External provider calls are isolated behind provider interfaces so most tests can run deterministically without internet access, provider credentials, or API cost.

## License

No license has been selected yet.
