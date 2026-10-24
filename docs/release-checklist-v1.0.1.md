# v1.0.1 release checklist

## Prepare the release commit

- [ ] Confirm `main` contains the reviewed `v1.0.1` source version,
  README, changelog, and release notes.
- [ ] Confirm FastAPI and OpenTelemetry versions match the package version.
- [ ] Confirm all required checks pass on the exact release commit:
  CI/Pytest, project style, repository preflight, CodeQL,
  container security, demo, kind, and Terraform.
- [ ] Confirm the main-branch container release workflow succeeded.
- [ ] Record the `sha-<commit>` image and its published OCI digest.
- [ ] Confirm an anonymous immutable image pull and container smoke test.
- [ ] Confirm the SPDX SBOM and provenance attestations were generated.

## Create the versioned release

- [ ] Create and push an **annotated** `v1.0.1` tag on the verified
  release commit (do not move the existing `v1.0.0` tag).
- [ ] Confirm the `v1.0.1` tagged release workflow finishes successfully.
- [ ] Confirm `ghcr.io/joshuabisdorf/secure-ai-gateway:v1.0.1` exists
  and is anonymously pullable.
- [ ] Record the exact OCI digest and verify the tagged-run SHA and version
  tags resolve to the same digest.
- [ ] Verify associated build provenance and SPDX SBOM attestations.
- [ ] Create the GitHub `v1.0.1` release using
  [the patch release notes](release-notes-v1.0.1.md).
- [ ] Ensure the release links the correct annotated tag and immutable image.

## Release by digest

Prefer deployment of the immutable OCI digest recorded by the successful
tagged run. Do not assume that an earlier main-branch build has the same
digest: mutable base-image tags and package upgrades can change the result.
