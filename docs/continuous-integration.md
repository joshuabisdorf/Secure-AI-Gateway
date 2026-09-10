# Continuous integration

Secure AI Gateway uses GitHub Actions to enforce deterministic security and build gates on every push to `main` and on every pull request.

Workflow:

```text
.github/workflows/ci.yml
```

The workflow also supports manual `workflow_dispatch` runs.

## Security posture

CI intentionally does not require provider, PostgreSQL, Redis, or gateway-client secrets.

The test suite forces the mock provider plus in-memory rate-limit and usage-accounting backends. The prompt-injection benchmark is fully offline. The Docker job builds the production image but does not start it or make provider calls.

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

This covers authentication, policy enforcement, rate limiting, usage budgets, PII handling, prompt-injection detection, system-prompt leakage evaluation logic, security-policy profiles, tool authorization, and provider normalization without calling real upstream providers.

### Prompt-injection benchmark

The `Prompt-injection benchmark` job runs:

```bash
python -m app.evals.prompt_injection_benchmark \
  --enforce-baseline \
  --show-errors
```

The job fails when the committed detector drops below the versioned benchmark thresholds for precision or recall, or exceeds the maximum benchmark false-positive rate. Error output includes case IDs only; prompt bodies are not printed.

The benchmark is a regression gate, not a claim that its thresholds are sufficient for production.

### Docker build

The `Docker build` job runs a clean image build from the committed Dockerfile:

```bash
docker build --tag secure-ai-gateway:ci .
```

It validates that the production image remains buildable independently of a developer workstation. It does not push an image or require registry credentials.

## Concurrency

The workflow cancels an older in-progress run when a newer commit arrives for the same workflow/ref. This prevents obsolete CI work from consuming runner time while preserving independent runs across branches and pull requests.

## Dependency actions

The workflow uses current major releases of the official GitHub actions for checkout and Python setup. Major-version references receive compatible security and maintenance updates within that release line. A later supply-chain-hardening milestone can pin all third-party actions to immutable full commit SHAs and automate controlled updates.

## Branch protection

Once the repository development flow uses pull requests consistently, the three jobs are intended to become required status checks before merging:

```text
Pytest
Prompt-injection benchmark
Docker build
```

Branch-protection policy is repository administration, not application runtime behavior, and should be configured separately from the gateway code.
