# Contributing

Secure AI Gateway is a security-focused open-source project. Changes should preserve its security invariants and the zero-cost default verification path.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
make install
make check
```

For the deterministic end-to-end path:

```bash
make demo
```

For distributed Kubernetes verification when Docker and kind are available:

```bash
make kind-up
make kind-verify
```

## Security invariants

Changes must preserve these boundaries unless the architecture documentation and tests are deliberately changed at the same time:

1. Raw gateway API keys are never stored; only one-way digests are persisted.
2. Provider credentials remain separate from client credentials.
3. Model-produced tool calls are untrusted output.
4. No side-effecting tool execution may be introduced without execution-time authorization in the same change.
5. Authorization uses authenticated identity and current policy, not model text or system-prompt secrecy.
6. Required security state fails closed when unavailable.
7. Audit events and telemetry must not contain prompts, raw PII, API/provider credentials, raw tool arguments/results, or execution tickets.
8. The default development and verification path must remain zero-cost; paid cloud resources are optional/reference-only and must require explicit opt-in.
9. Docker/Kubernetes teardown examples must not delete persistent volumes by default.

## Tests

At minimum, application changes should pass:

```bash
make check
```

Changes that touch Docker/runtime policy should also run:

```bash
make demo
```

Changes that touch Redis/PostgreSQL dependency behavior should run:

```bash
make resilience
```

Kubernetes changes should render and validate both CI and cloud Kustomize targets and, when practical, pass the local kind verification.

Terraform changes must remain format-clean and validate with the remote backend disabled. Do not add CI `apply` steps or long-lived AWS credentials.

## RME docstrings

Project functions use RME-style documentation:

- **Requires** — preconditions;
- **Modifies** — state/resources changed;
- **Effects** — externally visible effects;
- **Inputs** — inputs;
- **Outputs** — return/produced values.

When a function creates a file, record that as an effect. When it changes an existing file/resource, record it under Modifies. A returned path belongs under Outputs.

## Dependencies and CI

Third-party GitHub Actions must remain pinned to immutable full commit SHAs. Python dependency changes should pass Bandit, `pip-audit`, the CycloneDX generation check, and the normal test/evaluation gates.

Do not suppress a security scanner or regression benchmark merely to make CI green without documenting why the finding is a false positive or why a baseline should change.

## License and contributions

Secure AI Gateway is licensed under the Apache License 2.0. Unless explicitly stated otherwise, contributions intentionally submitted for inclusion in the project are provided under the terms described in Section 5 of that license. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Security reports

Do not open a public issue containing an exploitable vulnerability, secret, or credential. Follow [`SECURITY.md`](SECURITY.md).
