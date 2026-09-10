# Secure AI Gateway

Secure AI Gateway is a security-focused proxy between applications and LLM providers or local model backends. It centralizes authentication, authorization, rate limiting, usage budgets, sensitive-data controls, prompt-injection controls, tool permissions, provider routing, audit logging, request correlation, and security evaluation before requests reach an upstream model.

## Current capabilities

Implemented:

- FastAPI `/health` and OpenAI-style `/v1/chat/completions`
- deterministic non-network mock provider
- OpenAI and OpenRouter upstream providers
- structured high-entropy gateway API keys with SHA-256 verification
- PostgreSQL-backed client/key registry, immediate key revocation, and atomic rotation
- deployment-wide model ceiling plus per-client model grants
- Redis-backed distributed per-client rate limiting
- per-client UTC-day token/cost budgets with PostgreSQL persistence
- structured PII recognition for email, U.S. SSN, phone, and Luhn-valid payment-card values
- local semantic/contextual PII recognition for person names, personal locations, dates of birth, and street addresses
- per-client PII `redact` and `deny` policies
- deterministic prompt-injection `audit`, `deny`, and `off` policies
- synthetic system-prompt leakage evaluation with disposable canaries
- least-privilege function-tool exposure authorization
- OpenAI-compatible function `tools`, `tool_choice`, tool-result messages, and assistant `tool_calls`
- versioned named security-policy profiles
- versioned prompt-injection and semantic-PII benchmarks with precision/recall/FPR/FNR metrics
- hardened Dockerized gateway with PostgreSQL/Redis Compose integration
- GitHub Actions CI gates for pytest, both security benchmarks, and Docker builds
- structured one-line JSON audit events with request/client attribution
- sanitized provider failures

The next security milestone is **execution-time tool authorization**. Observability and deployment infrastructure follow after that control.

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
  +-- structured PII detection
  +-- local semantic/contextual PII detection
  +-- PII redaction or denial
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
SAG_SEMANTIC_PII_BACKEND=spacy

POSTGRES_PASSWORD=sag_dev_password
DATABASE_URL=postgresql://sag:sag_dev_password@127.0.0.1:5432/secure_ai_gateway
REDIS_URL=redis://127.0.0.1:6379/0

# Deployment-wide hard ceiling.
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

`.env`, `.client.env`, and `config/security-policies.json` are ignored by Git and excluded from the Docker build context.

## Unified security policy profiles

Normal per-client security configuration is grouped in one versioned registry:

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

Validate it with:

```bash
set -a
source .env
set +a
python -m app.policy_cli validate
```

When `SAG_SECURITY_POLICY_FILE` is enabled, it is authoritative. Invalid or unavailable policy does not fall back to stale legacy grants. `SAG_ALLOWED_MODELS` remains a separate deployment-wide ceiling, so a client profile can narrow model access but cannot widen it.

See `docs/security-policy-profiles.md`.

## PII detection and redaction

The PII stack runs before prompt-injection inspection and provider forwarding.

### Structured layer

The deterministic layer detects:

- email addresses
- U.S. SSNs in `NNN-NN-NNNN` form
- common North American phone-number formats
- 13–19 digit payment-card candidates that pass Luhn validation

### Semantic/contextual layer

The second layer runs locally with spaCy `en_core_web_sm` and recognizes supported entities only in personal context:

```text
person_name        -> [REDACTED_PERSON]
personal_location  -> [REDACTED_LOCATION]
date_of_birth      -> [REDACTED_DOB]
street_address     -> [REDACTED_ADDRESS]
```

Structured redaction runs first, then semantic analysis sees only the already-sanitized copy. The semantic model is installed with the project/container and does not send message text to a remote PII service.

Named-entity recognition alone is not considered sufficient evidence of private data. Contextual rules distinguish patterns such as `my name is`, `patient`, `I live in`, `DOB`, or delivery/home-address language from ordinary references to public people, cities, dates, and example addresses.

If the semantic model cannot load, PII inspection fails closed with `503 PII detection is unavailable.` It does not silently fall back to structured-only protection.

The existing profile action applies to the combined stack:

```text
redact  replace structured and semantic findings in the copied provider request
deny    reject when either layer detects PII
```

Audit records receive only safe type/count metadata. Raw detected values are not copied into audit events.

This is an English-focused first semantic layer, not comprehensive PII/PHI recognition. It can miss unusual phrasing and unsupported entity types, and contextual recognizers can still over-redact. It does not yet cover broad clinical entities, arbitrary account identifiers, relationship inference, multilingual PII, or cross-message identity resolution.

See `docs/semantic-pii.md`.

## Semantic PII benchmark

The version-1 benchmark is fully offline:

```bash
python -m app.evals.semantic_pii_benchmark --show-errors
python -m app.evals.semantic_pii_benchmark --enforce-baseline --show-errors
```

Dataset:

```text
evals/datasets/semantic_pii_v1.json
```

It contains 52 curated cases: 32 supported personal-context positives and 20 hard negatives involving public people, ordinary locations/dates, and public/example/fictional addresses.

The measured v1 baseline on the clean GitHub Actions runner when introduced was:

```text
TP=32  FP=0  TN=20  FN=0
precision=1.0000
recall=1.0000
false_positive_rate=0.0000
false_negative_rate=0.0000
```

Those numbers describe this deliberately scoped regression dataset only. They are **not** an estimate of real-world PII-detection accuracy or evidence that the detector is complete. Meaningful dataset/label changes should become `semantic_pii_v2.json` rather than silently rewriting v1.

## Prompt-injection detection and benchmark

`profile.prompt_injection_action` is `audit`, `deny`, or `off`. The deterministic baseline identifies direct instruction override, system/developer prompt extraction, role impersonation, safety/policy bypass, secret-exfiltration requests, and bounded Base64/hex content that decodes to a direct indicator.

The score is a deterministic rule score, not a probability. Raw prompts and matched fragments are not added to audit logs.

Run its versioned 66-case benchmark with:

```bash
python -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
```

The initial v1 baseline is:

```text
TP=36  FP=7  TN=13  FN=10
precision=0.8372
recall=0.7826
false_positive_rate=0.3500
false_negative_rate=0.2174
```

The benchmark deliberately exposes current weaknesses including typoglycemia, split multi-turn attacks, and false positives on quoted/descriptive security text.

See `docs/prompt-injection-benchmark.md`.

## Function-tool authorization

`profile.allowed_tools` lists exact function names an authenticated client may expose to the model. There is no wildcard grant; an empty list means no tools. A named `tool_choice` must refer to a declared and permitted function.

This control authorizes **tool exposure**, not execution. The gateway currently proxies function definitions and model-generated `tool_calls`; it does not execute them. Model output is untrusted data. The next milestone adds a separate execution-time authorization boundary before any tool side effect is allowed.

See `docs/tool-authorization.md`.

## Other security controls

A requested model must pass both `SAG_ALLOWED_MODELS` and the client profile's `allowed_models` grant. Redis provides distributed fixed-window per-client quotas. PostgreSQL stores client/key records and UTC-day usage totals. Usage budgets are checked before provider work and provider-reported usage is recorded after completion.

The audit layer intentionally omits raw gateway keys, prompts/messages, matched injection fragments, detected PII values, provider credentials, function arguments, tool-result content, and upstream response bodies.

The live system-prompt leakage evaluator uses only synthetic protected text and disposable canaries:

```bash
python -m app.evals.system_prompt_leakage --live --model openrouter/free
```

A passing leakage run means no tested synthetic canary leakage was observed; system prompts are not treated as a secrecy or authorization boundary. See `docs/system-prompt-leakage.md`.

## Dockerized local stack

Build and run the gateway, PostgreSQL, and Redis:

```bash
docker compose up -d --build
docker compose ps
curl -i http://127.0.0.1:8000/health
```

The gateway container runs as a non-root user with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, a small writable `/tmp`, and a read-only policy-file mount. The local spaCy model is installed during the image build; no first-request model download is required.

For routine shutdown, preserve persistent state:

```bash
docker compose down
```

Do not use `docker compose down -v` unless intentionally deleting PostgreSQL and Redis data.

See `docs/docker.md`.

## Continuous integration

GitHub Actions runs four independent gates on pushes to `main` and pull requests:

```text
Pytest
Prompt-injection benchmark
Semantic PII benchmark
Docker build
```

The workflow is `.github/workflows/ci.yml`. It grants only `contents: read`, requires no provider/database/Redis secrets, and makes no live LLM calls.

Run the equivalent gates locally with:

```bash
pytest -q
python -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
python -m app.evals.semantic_pii_benchmark --enforce-baseline --show-errors
docker build --tag secure-ai-gateway:ci .
```

See `docs/continuous-integration.md`.

## Development setup

Requirements:

- Python 3.13+
- Docker with Compose
- Git

Install or refresh dependencies after pulling changes:

```bash
python -m pip install -e '.[dev]'
```

For direct host-side development:

```bash
docker compose up -d postgres redis
set -a
source .env
set +a
python -m app.policy_cli validate
python -m app.database migrate
uvicorn app.main:app --reload --env-file .env
```

For containerized integration testing:

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
├── .github/workflows/ci.yml
├── app/
│   ├── evals/
│   │   ├── prompt_injection_benchmark.py
│   │   ├── semantic_pii_benchmark.py
│   │   └── system_prompt_leakage.py
│   ├── policies/
│   ├── providers/
│   ├── pii.py
│   ├── semantic_pii.py
│   └── ...
├── config/security-policies.example.json
├── db/migrations/
├── docker/entrypoint.sh
├── docs/
│   ├── continuous-integration.md
│   ├── docker.md
│   ├── prompt-injection-benchmark.md
│   ├── security-policy-profiles.md
│   ├── semantic-pii.md
│   ├── system-prompt-leakage.md
│   └── tool-authorization.md
├── evals/datasets/
│   ├── prompt_injection_v1.json
│   └── semantic_pii_v1.json
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
- [x] semantic PII detection/evaluation
- [x] deterministic prompt-injection detection
- [x] system-prompt leakage tests
- [x] least-privilege function-tool exposure authorization
- [ ] execution-time tool authorization

### Evaluation and infrastructure

- [x] versioned adversarial prompt dataset and measurable detection metrics
- [x] versioned semantic PII benchmark and regression gate
- [x] Dockerized gateway
- [x] GitHub Actions CI
- [ ] OpenTelemetry / Prometheus
- [ ] Kubernetes
- [ ] Terraform
- [ ] cloud deployment

## Function documentation convention

Project functions use RME-style docstrings:

- **Requires** — conditions that must hold before execution
- **Modifies** — state/resources changed
- **Effects** — externally visible side effects
- **Inputs** — function inputs
- **Outputs** — returned or produced outputs

## License

No license has been selected yet.
