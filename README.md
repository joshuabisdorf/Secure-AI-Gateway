# Secure AI Gateway

A security-focused gateway that sits between applications and large language model providers or local models.

The project is intended to provide a centralized enforcement point for authentication, authorization, usage controls, data protection, model access, tool permissions, and security telemetry before requests reach an LLM provider.

## Project Status

Early development.

The current codebase provides:

- FastAPI application foundation
- `/health` endpoint
- automated tests with `pytest`
- isolated Python project configuration

The next milestone is an OpenAI-compatible `/v1/chat/completions` endpoint backed by a provider abstraction and a fake provider for deterministic testing.

## Planned Architecture

```text
Application
    |
    v
Secure AI Gateway
    |
    +-- Authentication
    +-- Rate limiting
    +-- Model allowlists
    +-- Token and cost budgets
    +-- PII detection/redaction
    +-- Prompt-injection detection
    +-- Tool permissions
    +-- Policy engine
    +-- Audit logging
    +-- Model routing
    |
    v
OpenAI / Anthropic / Local Models
```

The gateway is designed so that applications authenticate to the gateway rather than receiving direct access to upstream provider credentials.

## Security Goals

Planned controls include:

- API-key authentication
- per-user rate limits
- model allowlists
- per-request and per-user token/cost budgets
- structured security audit logs
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

LLM output will be treated as untrusted input. A model may request an action, but authorization will be enforced by code outside the model.

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
│   └── main.py
├── tests/
│   └── test_health.py
├── .gitignore
├── pyproject.toml
└── README.md
```

This layout will expand as provider, policy, authentication, audit, and detection components are introduced.

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

### Run the tests

```bash
pytest -q
```

### Run the gateway

```bash
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

FastAPI documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## Development Across LMDE and WSL2

The project is developed from separate Linux working copies on LMDE and Ubuntu under WSL2.

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

Typical workflow before starting work:

```bash
git pull
source .venv/bin/activate
```

Typical workflow after completing a tested change:

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

Example:

```python
def health() -> dict[str, str]:
    """
    RME

    Requires:
        - The FastAPI application is running.

    Modifies:
        - Nothing.

    Effects:
        - Reports the health status of the gateway.

    Inputs:
        - None.

    Outputs:
        - A dictionary containing the gateway health status.
    """
    return {"status": "ok"}
```

## Roadmap

### Phase 1 — Gateway foundation

- [x] FastAPI application
- [x] health endpoint
- [x] automated test foundation
- [ ] OpenAI-compatible chat-completions schema
- [ ] provider interface
- [ ] fake provider
- [ ] real upstream provider

### Phase 2 — Core security controls

- [ ] API-key authentication
- [ ] model allowlists
- [ ] policy engine
- [ ] structured audit logging
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

External provider calls will be isolated behind provider interfaces so most tests can run deterministically without internet access, provider credentials, or API cost.

## License

No license has been selected yet.
