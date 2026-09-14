PYTHON ?= python

.PHONY: help install release-install lock-verify style
.PHONY: test evals security check preflight demo resilience
.PHONY: m10-adversarial m10-integration benchmark
.PHONY: up down kind-up kind-verify terraform-validate

help:
	@printf '%s\n' \
	  'install             Install project with development tooling' \
	  'release-install     Install exact runtime dependencies from release lock' \
	  'lock-verify         Validate release dependency lock invariants' \
	  'style               Enforce project-owned source style rules' \
	  'test                Run pytest' \
	  'evals               Run prompt-injection and semantic-PII baselines' \
	  'security            Run Bandit and dependency audit' \
	  'check               Run style + test + evals + security' \
	  'preflight           Run repository/release hygiene checks' \
	  'demo                Run the zero-cost end-to-end portfolio demo' \
	  'resilience          Inject backend and telemetry outages' \
	  'm10-adversarial     Run deterministic M10 adversarial coverage' \
	  'm10-integration     Run real Redis/PostgreSQL concurrency tests' \
	  'benchmark           Run local two-replica runtime benchmark' \
	  'up                  Start the Docker Compose stack' \
	  'down                Stop Compose without deleting volumes' \
	  'kind-up             Build/start the local kind environment' \
	  'kind-verify         Verify policy, resilience, and load in kind' \
	  'terraform-validate  Format-check and validate Terraform roots'

install:
	$(PYTHON) -m pip install -e '.[dev]'

release-install: lock-verify
	$(PYTHON) -m pip install -r requirements/release.lock
	$(PYTHON) -m pip install --no-deps --no-build-isolation -e .
	$(PYTHON) -m pip check

lock-verify:
	$(PYTHON) scripts/verify_release_lock.py

style:
	$(PYTHON) scripts/style_check.py

test:
	pytest -q

evals:
	$(PYTHON) -m app.evals.prompt_injection_benchmark \
	  --enforce-baseline \
	  --show-errors
	$(PYTHON) -m app.evals.semantic_pii_benchmark \
	  --enforce-baseline \
	  --show-errors

security:
	bandit -q -r app -ll -ii
	$(PYTHON) -m pip_audit --progress-spinner off --skip-editable

check: style test evals security

preflight:
	$(PYTHON) scripts/repo_preflight.py

demo:
	bash scripts/demo.sh

resilience:
	bash scripts/resilience-smoke.sh

m10-adversarial:
	pytest -q tests/test_m10_adversarial.py

m10-integration:
	@test -n "$$DATABASE_URL" || \
	  (echo 'DATABASE_URL is required.' >&2; exit 1)
	@test -n "$$REDIS_URL" || \
	  (echo 'REDIS_URL is required.' >&2; exit 1)
	SAG_RUN_M10_INTEGRATION=1 pytest -q tests/test_m10_integration.py

benchmark:
	@test -n "$$DATABASE_URL" || \
	  (echo 'DATABASE_URL is required.' >&2; exit 1)
	@test -n "$$REDIS_URL" || \
	  (echo 'REDIS_URL is required.' >&2; exit 1)
	$(PYTHON) scripts/m10_runtime_verification.py

up:
	docker compose up -d --build

down:
	docker compose down

kind-up:
	bash scripts/k8s-local-up.sh

kind-verify:
	bash scripts/k8s-verify.sh

terraform-validate:
	terraform fmt -check -diff -recursive terraform
	terraform -chdir=terraform/bootstrap init \
	  -backend=false \
	  -input=false
	terraform -chdir=terraform/bootstrap validate -no-color
	terraform -chdir=terraform/aws init \
	  -backend=false \
	  -input=false
	terraform -chdir=terraform/aws validate -no-color
