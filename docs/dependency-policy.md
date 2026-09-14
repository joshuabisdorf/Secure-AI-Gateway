# Dependency and build reproducibility policy

Secure AI Gateway separates the dependency declaration used for development from the exact dependency set used for release builds.

## Locking strategy

`pyproject.toml` remains the developer-facing source of direct runtime requirements and supported version ranges. `requirements/release.lock` is the release-runtime lock: every registry dependency is pinned to an exact version, and the direct spaCy model wheel is pinned to both its immutable release URL and SHA-256 digest.

The build backend is also exact-pinned in `pyproject.toml`. This prevents build-isolation from silently selecting a newer setuptools release while the runtime dependency set remains unchanged.

The lock deliberately excludes development-only tools such as pytest, Bandit, and pip-audit. Development continues to use:

```bash
make install
```

A release-style editable installation uses the same exact runtime set as the container build:

```bash
make release-install
```

That command validates the lock, installs the exact dependency set, installs Secure AI Gateway with dependency resolution and build isolation disabled, and runs `pip check`.

## Clean-install verification

CI creates a new virtual environment on Python 3.13 and performs the same sequence from the committed lock. The Docker build consumes the lock on Python 3.14 before installing the application. Together these paths verify the supported lower development baseline and the current container runtime rather than only reusing a developer environment.

`python scripts/verify_release_lock.py` fails when:

- a registry entry is not exact-pinned;
- a direct URL is missing a SHA-256 fragment;
- a dependency declared by `pyproject.toml` is absent from the release lock;
- duplicate lock entries exist;
- the build backend drifts from its exact pin.

## Refresh procedure

Lock refreshes are deliberate maintenance changes, not an install-time side effect. To refresh:

1. create a clean Python 3.13 environment;
2. resolve the production dependencies from `pyproject.toml`;
3. replace the exact versions in `requirements/release.lock` with the reviewed resolved set;
4. preserve or update the SHA-256 pin for every direct artifact;
5. run `make lock-verify`, `make check`, and a container build;
6. allow the clean release-install CI job and vulnerability scans to complete before merging.

A lock refresh should be isolated from unrelated feature work when practical so dependency changes remain reviewable.

## Update policy

Dependabot remains the automated signal for Python, GitHub Actions, and Docker updates. Automated proposals do not bypass verification. Dependency changes must pass unit/security regression tests, `pip-audit`, CodeQL where applicable, container scanning, the clean lock installation, and the Docker build.

Runtime Python dependencies are normally reviewed when Dependabot proposes an update or when a security advisory requires action. The release lock is refreshed after an accepted direct-dependency update so transitive versions are recorded explicitly rather than floating until the next build.

Container base and service images are reviewed on the same basis. The production image uses a Python patch-level tag and is rebuilt under Trivy scanning; local PostgreSQL/Redis versions remain explicit in deployment configuration. Image changes must pass Compose, kind, and container-security verification before release. The immutable digest of each published release image is the deployment identity; mutable registry tags are convenience references, not the trust anchor.

## Hash-pinning scope

The repository uses immutable commit SHAs for third-party GitHub Actions. Direct downloadable Python artifacts are SHA-256 pinned. Registry-hosted Python packages are exact-version locked but are not currently duplicated with every wheel hash because the project supports more than one Python/runtime platform and the lock is intentionally readable and maintainable. Release provenance and the image digest cover the resulting container artifact.
