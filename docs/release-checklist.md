# Release checklist

Use this checklist for the first portfolio `v1.0.0` release.

## Repository

- [ ] `main` is the intended release commit.
- [ ] README reflects current capabilities and limitations.
- [ ] `SECURITY.md` and `docs/security-review.md` are current.
- [ ] `CHANGELOG.md` contains the release summary.
- [ ] No secrets, local `.env` files, Terraform state, or generated credentials are tracked.
- [ ] A software license has been deliberately selected, or the repository intentionally remains unlicensed.

## Verification

- [ ] `make install`
- [ ] `make check`
- [ ] `make demo`
- [ ] `make kind-up && make kind-verify` when kind is available.
- [ ] Terraform validation is green in CI.
- [ ] GitHub CI is green on the release commit.
- [ ] Security analysis is green on the release commit.

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
