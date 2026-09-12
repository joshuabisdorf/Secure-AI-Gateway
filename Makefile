PYTHON ?= python

.PHONY: help install test evals security check demo up down kind-up kind-verify terraform-validate

help:
	@printf '%s\n' \
	  'install             Install project with development tooling' \
	  'test                Run pytest' \
	  'evals               Run prompt-injection and semantic-PII baselines' \
	  'security            Run Bandit and dependency audit' \
	  'check               Run test + evals + security' \
	  'demo                Run the zero-cost end-to-end portfolio demo' \
	  'up                  Start the Docker Compose stack' \
	  'down                Stop the Docker Compose stack without deleting volumes' \
	  'kind-up             Build/start the local kind environment' \
	  'kind-verify         Verify the local kind environment' \
	  'terraform-validate  Format-check and validate both Terraform roots'

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	pytest -q

evals:
	$(PYTHON) -m app.evals.prompt_injection_benchmark --enforce-baseline --show-errors
	$(PYTHON) -m app.evals.semantic_pii_benchmark --enforce-baseline --show-errors

security:
	bandit -q -r app -ll -ii
	$(PYTHON) -m pip_audit --progress-spinner off --skip-editable

check: test evals security

demo:
	bash scripts/demo.sh

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
	terraform -chdir=terraform/bootstrap init -backend=false -input=false
	terraform -chdir=terraform/bootstrap validate -no-color
	terraform -chdir=terraform/aws init -backend=false -input=false
	terraform -chdir=terraform/aws validate -no-color
