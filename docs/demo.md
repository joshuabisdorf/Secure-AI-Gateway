# Demo

The portfolio demo is designed to prove the gateway's core security properties without a paid provider, cloud account, or disposable infrastructure.

Run it from the repository root:

```bash
make demo
```

The demo uses the deterministic `mock` provider and the tracked `config/demo-security-policies.json` policy. It starts the Docker Compose stack, creates a temporary PostgreSQL-backed gateway client, exercises the security path, and revokes the temporary key on exit. Existing Docker volumes are preserved.

It verifies:

1. gateway health;
2. PostgreSQL-backed client creation and authenticated chat;
3. persistent daily usage accounting;
4. structured PII detection/redaction;
5. prompt-injection detection in audit mode;
6. tool-call validation and signed execution-ticket issuance;
7. independent execution-time authorization;
8. one-time replay denial;
9. distributed Redis rate limiting;
10. Prometheus metric exposure.

A successful run ends with:

```text
=== RESULT ===
secure_ai_gateway_demo=PASS
provider_calls=mock_only
billable_cloud_resources=0
demo_key_revoked_on_exit=true
stack_left_running=true
next_cleanup_command=make down
```

The script deliberately does not print the raw gateway API key or execution ticket. The raw key exists only in process memory for the duration of the demo. The generated key is revoked during shell cleanup even when a later assertion fails.

The stack is left running so a reviewer can inspect `/metrics`, Prometheus, logs, or the API. Stop it without deleting persistent data:

```bash
make down
```

Do not use `docker compose down -v` unless you intentionally want to destroy the local PostgreSQL, Redis, and Prometheus volumes.

## Requirements

The demo expects:

- Docker with `docker compose`;
- `curl`;
- Python 3.13 or later.

No AWS account, provider API key, billing method, or live LLM call is required.

## What the demo does not prove

The demo is a deterministic local security demonstration, not a production certification. It does not prove:

- real-provider availability or behavior;
- cloud infrastructure correctness after an actual Terraform apply;
- universal prompt-injection or PII detection accuracy;
- Internet-facing TLS, WAF, DNS, or ingress configuration;
- execution of real external tools. The gateway authorizes tool execution but does not implement side-effecting executors.
