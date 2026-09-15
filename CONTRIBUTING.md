# Contributing

Secure AI Gateway is a security-focused open-source project. Changes should
preserve its security invariants and the zero-cost default verification path.

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

Changes must preserve these boundaries unless architecture documentation and
tests are deliberately changed at the same time:

1. Raw gateway API keys are never stored; only one-way digests are persisted.
2. Provider credentials remain separate from client credentials.
3. Model-produced tool calls are untrusted output.
4. Side-effecting tools require independent execution-time authorization.
5. Authorization uses authenticated identity and current policy, not model text
   or system-prompt secrecy.
6. Required security state fails closed when unavailable.
7. Audit events and telemetry exclude prompts, raw PII, credentials, raw tool
   arguments/results, and execution tickets.
8. The default development and verification path remains zero-cost. Paid cloud
   resources are optional/reference-only and require explicit opt-in.
9. Docker/Kubernetes teardown examples do not delete persistent volumes by
   default.

## Project style

The repository uses its own style standard in
[`docs/style-standard.md`](docs/style-standard.md). It is intentionally
self-contained rather than adopting an external style policy.

Run the machine-checkable rules with:

```bash
make style
```

The 80-character line limit is a hard default. Only genuinely indivisible
values, such as URLs, immutable hashes, or other atomic values that cannot be
sensibly split, may exceed it. Do not add broad exclusions to avoid wrapping
ordinary source or prose.

RMEIO remains the project's documentation contract for non-trivial runtime
functions. Security boundaries, persistence operations, credential operations,
policy checks, network operations, and CLI actions require the full contract.

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

Kubernetes changes should render and validate both CI and cloud Kustomize
targets and, when practical, pass local kind verification.

Terraform changes must remain format-clean and validate with the remote backend
disabled. Do not add CI `apply` steps or long-lived AWS credentials.

## RMEIO docstrings

Project functions use RMEIO documentation:

- **Requires** -- preconditions;
- **Modifies** -- state/resources changed;
- **Effects** -- externally visible effects;
- **Inputs** -- inputs;
- **Outputs** -- returned or produced values.

When a function creates a file, record that as an effect. When it changes an
existing file or resource, record it under Modifies. A returned path belongs
under Outputs.

## Dependencies and CI

Third-party GitHub Actions remain pinned to immutable full commit SHAs. Python
dependency changes should pass Bandit, `pip-audit`, CycloneDX generation, and
the normal test/evaluation gates.

Do not suppress a security scanner or regression benchmark merely to make CI
green without documenting why the finding is a false positive or why a
baseline should change.

## License and contributions

Secure AI Gateway is licensed under the Apache License 2.0. Unless explicitly
stated otherwise, contributions intentionally submitted for inclusion in the
project are provided under the terms described in Section 5 of that license.
See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Security reports

Do not open a public issue containing an exploitable vulnerability, secret, or
credential. Follow [`SECURITY.md`](SECURITY.md).
