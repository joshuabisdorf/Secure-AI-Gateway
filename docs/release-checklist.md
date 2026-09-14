# Release checklist

Use this checklist for the first stable `v1.0.0` release.

## Repository

- [ ] `main` is the intended release commit.
- [ ] README reflects current capabilities and limitations.
- [ ] `SECURITY.md` and `docs/security-review.md` are current.
- [ ] `CHANGELOG.md` contains the release summary.
- [ ] `docs/architecture.md` and `docs/portfolio-evidence.md` reflect the final
  design/evidence path.
- [ ] `LICENSE` contains the Apache License 2.0 text and `NOTICE` contains the
  current project attribution.
- [ ] No secrets, local `.env` files, Terraform state, generated credentials, or
  Python build/cache artifacts are tracked.
- [ ] `make preflight` passes on the intended release state.
- [ ] `python scripts/repo_preflight.py --history` passes from a full clone,
  confirming no sensitive-looking filenames were committed historically.

## Dependencies and build reproducibility

- [ ] `make lock-verify` passes.
- [ ] The `Reproducible release install` CI job is green from a clean
  environment.
- [ ] `requirements/release.lock` reflects the reviewed runtime dependency set
  for the release.
- [ ] Direct downloaded artifacts retain their SHA-256 pins.
- [ ] Dependency and base-image update expectations in
  `docs/dependency-policy.md` are current.

## Verification

- [ ] `make check` passes on the intended release state.
- [ ] `End-to-end demo` is green on the release commit.
- [ ] `Resilience smoke` is green on the release commit.
- [ ] CodeQL Python analysis is green on the release commit.
- [ ] Container security / Trivy is green on the release commit.
- [ ] `kind security and resilience` is green, including default-deny
  enforcement, Pod Security, replica rescheduling, and bounded-load checks.
- [ ] Terraform validation is green in CI.
- [ ] All primary GitHub CI gates are green on the release commit.
- [ ] Optionally repeat `make demo`, `make resilience`, and
  `make kind-up && make kind-verify` locally as final workstation smoke tests.

## Release artifact

- [ ] Release workflow is green on the release commit.
- [ ] Immutable `sha-*` GHCR image exists and its OCI digest is recorded.
- [ ] Anonymous pull verification is green.
- [ ] Build-provenance attestation verifies with `gh attestation verify` for the
  image digest.
- [ ] SPDX SBOM attestation verifies with the SPDX predicate type documented in
  `docs/supply-chain.md`.
- [ ] Only after the checks above, create annotated tag `v1.0.0`.
- [ ] Confirm the workflow publishes
  `ghcr.io/joshuabisdorf/secure-ai-gateway:v1.0.0` and that it resolves to the
  reviewed digest.

## Explicit non-requirements

The following are not required for `v1.0.0` under the project's zero-cost
constraint:

- AWS account creation;
- Terraform apply;
- EKS/RDS/ElastiCache runtime verification;
- paid LLM-provider requests;
- public Internet ingress.
