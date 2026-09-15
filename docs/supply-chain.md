# Release supply-chain verification

The public container release is identified by its OCI digest. Tags are useful
aliases, but the digest is the immutable artifact identity used for verification
and deployment.

The `Release container` workflow builds and smoke-tests the image, pushes the
immutable `sha-<commit>` tag, resolves its registry digest, generates an SPDX
JSON SBOM from that digest, and creates GitHub artifact attestations for both
build provenance and the SBOM. The attestations are also pushed to GHCR as
OCI-associated artifacts. The workflow then logs out and verifies the image can
still be pulled anonymously.

Third-party workflow actions are referenced by immutable commit SHA. The release
job requires only repository-scoped GitHub permissions: package write,
artifact-attestation write, OIDC token issuance, and repository read. It does
not require AWS or LLM-provider credentials.

## Verify an image

Set the image and immutable SHA tag from a completed release workflow:

```bash
IMAGE=ghcr.io/joshuabisdorf/secure-ai-gateway
TAG=sha-<12-character-commit-prefix>
```

Resolve and inspect the digest directly from GHCR:

```bash
DIGEST="$(
  docker buildx imagetools inspect "$IMAGE:$TAG" --format '{{json .Manifest}}' \
    | jq -r '.digest'
)"
printf '%s@%s\n' "$IMAGE" "$DIGEST"
docker pull "$IMAGE@$DIGEST"
```

Verify the GitHub build-provenance attestation against this repository:

```bash
gh attestation verify "oci://$IMAGE@$DIGEST" \
  -R joshuabisdorf/Secure-AI-Gateway
```

Verify and inspect the SPDX SBOM attestation:

```bash
gh attestation verify "oci://$IMAGE@$DIGEST" \
  -R joshuabisdorf/Secure-AI-Gateway \
  --predicate-type https://spdx.dev/Document/v2.3 \
  --format json \
  --jq '.[].verificationResult.statement.predicate'
```

`gh attestation verify` checks the signed attestation and repository identity. A
successful verification is evidence that GitHub issued the attestation for the
specified digest from this repository; it is not a statement that every
dependency is vulnerability-free.

## Release evidence

A tagged release should retain these linked identities:

```text
Git commit -> sha-<commit> tag -> OCI digest -> provenance attestation
                                      |
                                      +-> SPDX SBOM attestation
```

The semver tag, for example `v1.0.0`, is published only after the same image has
already been built and pushed under the immutable commit tag. Consumers should
record the digest after resolving the release tag and deploy by digest where
their platform supports it.

## Compatibility and upgrades

Tagged stable releases follow semantic-versioning intent. Within a stable major
release, changes should preserve the documented OpenAI-compatible request
surface and configuration contracts unless a security correction requires
otherwise. Breaking API, policy-schema, or operational changes belong in a new
major release and must be called out in `CHANGELOG.md`.

Database migrations are explicit and run before the Kubernetes gateway rollout.
Operators upgrading a persistent deployment should read the changelog, back up
authoritative PostgreSQL data according to their environment, apply the
migration job, and then roll out the new image. Automatic database downgrade is
not promised.

The current project baseline is Python 3.13 or newer, with the release container
on Python 3.14. Kubernetes manifests are schema-validated against Kubernetes
1.37 in CI. The optional AWS Terraform architecture remains a reference
deployment and is not required to verify a release.

## What the attestations do not claim

The attestations establish artifact identity, repository/workflow provenance,
and the generated dependency inventory. They do not replace vulnerability
scanning, code review, runtime policy, or deployment-specific admission
controls. Trivy, CodeQL, `pip-audit`, repository preflight, the security
regression suites, and Kubernetes hardening remain independent release gates.
