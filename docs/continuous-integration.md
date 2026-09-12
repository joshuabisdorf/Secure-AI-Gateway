# Continuous integration

Secure AI Gateway uses GitHub Actions to enforce deterministic security and build gates on every push to `main` and on every pull request.

Workflow:

```text
.github/workflows/ci.yml
```

The workflow also supports manual `workflow_dispatch` runs.

## Security posture

CI intentionally does not require provider, PostgreSQL, Redis, gateway-client, or telemetry secrets.

The test suite forces the mock provider plus in-memory rate-limit, usage-accounting, and tool-replay backends, and explicitly disables OTLP export. The prompt-injection benchmark and semantic PII benchmark are fully offline. The semantic PII job runs the local spaCy model installed with the project; message text is not sent to a remote classifier. The Docker job validates Compose configuration and builds the production image but does not start the stack or make provider calls.

The workflow grants only:

```yaml
permissions:
  contents: read
```

No write permission is required for these gates.

## Gates

### Pytest

The `Pytest` job installs the project with development dependencies under Python 3.13 and runs:

```bash
pytest -q
```

This covers authentication, policy enforcement, rate limiting, usage budgets, structured and semantic PII handling, prompt-injection detection, system-prompt leakage evaluation logic, security-policy profiles, exposure/execution-time tool authorization, provider normalization, and observability privacy/cardinality behavior without calling real upstream providers or telemetry collectors.

### Prompt-injection benchmark

The `Prompt-injection benchmark` job runs:

```bash
python -m app.evals.prompt_injection_benchmark \
  --enforce-baseline \
  --show-errors
```

It fails when the committed detector drops below the versioned benchmark thresholds for precision or recall, or exceeds the maximum benchmark false-positive rate. Error output includes case IDs only; prompt bodies are not printed.

### Semantic PII benchmark

The `Semantic PII benchmark` job runs:

```bash
python -m app.evals.semantic_pii_benchmark \
  --enforce-baseline \
  --show-errors
```

It evaluates the local semantic/contextual PII analyzer against the versioned dataset and fails on precision, recall, or false-positive-rate regression. Output contains aggregate metrics, category names, and optional case IDs, not benchmark text or detected values.

Both benchmark gates are curated regression tests. Their passing thresholds and measured results are not estimates of production accuracy.

### Docker build

The `Docker build` job first validates the full Compose graph:

```bash
docker compose config --quiet
```

It then runs a clean image build from the committed Dockerfile:

```bash
docker build --tag secure-ai-gateway:ci .
```

This validates that the gateway image, Prometheus/collector service configuration references, and pinned local semantic PII model remain reproducible independently of a developer workstation. CI does not start or push the images and requires no registry credentials.

## Concurrency

The workflow cancels an older in-progress run when a newer commit arrives for the same workflow/ref. This prevents obsolete CI work from consuming runner time while preserving independent runs across branches and pull requests.

## Dependency actions

The workflow uses current major releases of the official GitHub actions for checkout and Python setup. A later supply-chain-hardening milestone can pin action references to immutable full commit SHAs and automate controlled updates.

## Branch protection

Once the repository development flow uses pull requests consistently, these four jobs are intended to become required status checks before merging:

```text
Pytest
Prompt-injection benchmark
Semantic PII benchmark
Docker build
```

Branch-protection policy is repository administration, not application runtime behavior, and should be configured separately from the gateway code.
