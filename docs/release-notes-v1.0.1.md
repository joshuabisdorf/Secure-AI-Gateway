# Secure AI Gateway v1.0.1

`v1.0.1` is a maintenance patch release following the first stable
`v1.0.0` release.

## Highlights

- **Container security:** Python 3.14.8 slim Bookworm, and upgrades of
  available Debian packages during the runtime image build.
- **Dependency maintenance:** locked `urllib3` 2.8.0,
  `certifi` 2026.10.19, and `setuptools` 84.2.0.
- **Supply-chain maintenance:** updated SHA-pinned CodeQL, SPDX SBOM,
  build-provenance, and SBOM-generation actions.
- **Verification:** refreshed Kubernetes CI actions and checks that reject
  prerelease Python base-image tags.
- **Developer documentation:** native Sphinx/reStructuredText docstrings
  replace RMEIO throughout application and Python utility modules, with
  matching repository style checks and regression tests.

## Compatibility

This release makes no intentional breaking changes to the OpenAI-compatible
chat API, execution authorization API, persisted database format, or required
gateway configuration. Existing operator security policies and deployment
settings retain their documented meaning.

The container image is rebuilt from a newer Python base image and updated
OS packages. Users should validate their environment against the release
digest and perform deployment smoke tests before rollout.

## Verification and image integrity

Release only from the reviewed, green `main` commit. The GitHub release
workflow publishes the commit-specific `sha-<commit>` image with an OCI digest,
an SPDX SBOM, provenance and SBOM attestations, and an anonymous pull test.

An annotated `v1.0.1` Git tag triggers a separate tagged workflow that
publishes `ghcr.io/joshuabisdorf/secure-ai-gateway:v1.0.1`; wait for that
workflow to pass before treating the version alias as published. Record the
image digest from the tagged run rather than assuming separate builds of the
same Git commit are byte-for-byte reproducible.

See [the release checklist](release-checklist-v1.0.1.md) and
[the supply-chain guide](supply-chain.md) for verification commands.

## Scope

This is a security and maintenance release, not a feature release.
The accepted residual risks and known architecture limitations recorded for
`v1.0.0` remain applicable unless superseded by later documentation.
