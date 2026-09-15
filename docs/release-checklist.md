# Release checklist

Use this checklist for the first stable `v1.0.0` release.

## Repository and review

- [ ] `main` is the intended release commit.
- [ ] `pyproject.toml` reports version `1.0.0`.
- [ ] README reflects current capabilities and limitations.
- [ ] `SECURITY.md` and `docs/security-review.md` are current.
- [ ] `CHANGELOG.md` and `docs/release-notes-v1.0.0.md` are current.
- [ ] `docs/architecture.md` reflects the final trust boundaries.
- [ ] `docs/portfolio-evidence.md` reflects the final evidence path.
- [ ] `LICENSE` contains Apache License 2.0 and `NOTICE` contains the current
  project attribution.
- [ ] `.gitignore` and `.dockerignore` exclude local secrets, state, and
  generated artifacts.
- [ ] `make style` passes.
- [ ] `make preflight` passes.
- [ ] `python scripts/repo_preflight.py --history` passes from a full clone.
- [ ] No superseded roadmap issue remains open.

## Dependencies and build reproducibility

- [ ] `make lock-verify` passes.
- [ ] `Reproducible release install` is green from a clean environment.
- [ ] `requirements/release.lock` contains the reviewed runtime dependency set.
- [ ] Direct downloaded artifacts retain SHA-256 pins.
- [ ] Dependency/base-image expectations in `docs/dependency-policy.md` are
  current.

## Verification

- [ ] `make check` passes on the intended release state.
- [ ] `End-to-end demo` is green on the release commit.
- [ ] `Resilience smoke` is green on the release commit.
- [ ] CodeQL Python analysis is green on the release commit.
- [ ] Container security / Trivy is green on the release commit.
- [ ] Project-owned style workflow is green on the release commit.
- [ ] Repository/history preflight is green on the release commit.
- [ ] `kind security and resilience` is green.
- [ ] Terraform validation is green in CI.
- [ ] M10 adversarial/reliability/performance verification is green.
- [ ] All primary GitHub CI gates are green on the release commit.

## Immutable release artifact

Before creating the stable tag:

- [ ] Main-branch release workflow is green on the release commit.
- [ ] Immutable `sha-*` GHCR image exists.
- [ ] Its OCI digest is recorded.
- [ ] Anonymous pull of the immutable SHA tag succeeds.
- [ ] Build provenance verifies with `gh attestation verify`.
- [ ] SPDX SBOM attestation verifies with the predicate documented in
  `docs/supply-chain.md`.

## Stable tag

Only after the immutable artifact and release commit are verified:

- [ ] Create annotated tag `v1.0.0` at the reviewed release commit.
- [ ] Confirm the tagged release workflow is green.
- [ ] Confirm `ghcr.io/joshuabisdorf/secure-ai-gateway:v1.0.0` exists.
- [ ] Confirm `:v1.0.0` resolves to the same OCI digest as the immutable
  SHA tag.
- [ ] Confirm an unauthenticated pull of `:v1.0.0` succeeds.
- [ ] Confirm an unauthenticated pull of the immutable SHA tag still succeeds.
- [ ] Record final release identities in `docs/portfolio-evidence.md` or the
  GitHub release page.
- [ ] Close M12 only after the stable release evidence is complete.

## Optional workstation smoke tests

These may be repeated locally after CI passes:

```bash
make demo
make resilience
make kind-up
make kind-verify
```

## Explicit non-requirements

The following are not required for `v1.0.0` under the zero-cost constraint:

- AWS account creation;
- Terraform apply;
- EKS/RDS/ElastiCache runtime verification;
- paid LLM-provider requests;
- public Internet ingress.
