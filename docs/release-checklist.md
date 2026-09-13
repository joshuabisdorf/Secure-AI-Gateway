# Release checklist

Use this checklist for the first portfolio `v1.0.0` release.

## Repository

- [ ] `main` is the intended release commit.
- [ ] README reflects current capabilities and limitations.
- [ ] `SECURITY.md` and `docs/security-review.md` are current.
- [ ] `CHANGELOG.md` contains the release summary.
- [ ] `docs/architecture.md` and `docs/portfolio-evidence.md` reflect the final design/evidence path.
- [ ] No secrets, local `.env` files, Terraform state, generated credentials, or Python build/cache artifacts are tracked.
- [ ] `make preflight` passes on the intended release state.
- [ ] `python scripts/repo_preflight.py --history` passes from a full clone, confirming no sensitive-looking filenames were committed historically.
- [ ] A software license has been deliberately selected, or the repository intentionally remains unlicensed.

## Verification

- [ ] `make check` passes on the intended release state.
- [ ] `End-to-end demo` is green on the release commit.
- [ ] `Resilience smoke` is green on the release commit.
- [ ] CodeQL Python analysis is green on the release commit.
- [ ] Container security / Trivy is green on the release commit.
- [ ] `make kind-up && make kind-verify` has passed for the current Kubernetes architecture when kind is available.
- [ ] Terraform validation is green in CI.
- [ ] All primary GitHub CI gates are green on the release commit.
- [ ] Optionally repeat `make demo` and `make resilience` locally as a final workstation smoke test.

## Release artifact

- [ ] Release workflow is green on the release commit.
- [ ] Immutable `sha-*` GHCR image exists.
- [ ] Anonymous pull verification is green.
- [ ] Only after the checks above, create annotated tag `v1.0.0`.
- [ ] Confirm the workflow publishes `ghcr.io/joshuabisdorf/secure-ai-gateway:v1.0.0`.

## Explicit non-requirements

The following are not required for `v1.0.0` under the project's zero-cost constraint:

- AWS account creation;
- Terraform apply;
- EKS/RDS/ElastiCache runtime verification;
- paid LLM-provider requests;
- public Internet ingress.
