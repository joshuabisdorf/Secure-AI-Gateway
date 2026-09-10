# Dockerized gateway

The gateway, PostgreSQL, and Redis can run as one local Docker Compose stack. The container setup is intended for reproducible local integration and as a foundation for CI/deployment work.

## Build and start

Create the ignored local policy file if it does not already exist:

```bash
cp config/security-policies.example.json config/security-policies.json
```

Keep provider credentials and deployment settings in the ignored project `.env`. Compose reads that file for variable substitution but does not copy it into the image.

Build the gateway image:

```bash
docker compose build gateway
```

Start the complete stack:

```bash
docker compose up -d
```

Do not use `docker compose down -v` for routine cleanup. The named PostgreSQL and Redis volumes contain persistent gateway state.

Inspect service state:

```bash
docker compose ps
```

The gateway is exposed only on `127.0.0.1:8000`. PostgreSQL and Redis remain exposed on their existing loopback-only development ports.

## Container startup sequence

Compose waits for the PostgreSQL and Redis health checks before starting the gateway. The tracked `docker/entrypoint.sh` then runs:

```text
python -m app.policy_cli validate
python -m app.database migrate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Policy validation and migrations must succeed before Uvicorn starts. The migration runner remains idempotent and verifies the SHA-256 digest of migrations that have already been recorded.

For a future multi-replica production deployment, schema migrations should move to a dedicated deployment/init job so multiple application replicas do not race to run migrations during startup.

## Container networking

Host development and container networking intentionally use different addresses:

```text
host process -> PostgreSQL: 127.0.0.1:5432
host process -> Redis:      127.0.0.1:6379

gateway container -> PostgreSQL: postgres:5432
gateway container -> Redis:      redis:6379
```

The Compose service overrides `DATABASE_URL`, `REDIS_URL`, and the policy-file path inside the gateway container. This means the same `.env` can still be used for direct host-side development.

## Policy file

The local policy registry is bind-mounted read-only:

```text
./config/security-policies.json
    -> /app/config/security-policies.json
```

`.dockerignore` excludes the ignored local policy file, `.env`, `.client.env`, the virtual environment, Git metadata, caches, and logs from the build context.

## Runtime hardening

The gateway container:

- runs as a dedicated non-root user (`uid=10001`)
- uses a read-only root filesystem
- drops all Linux capabilities
- enables `no-new-privileges`
- provides only a small writable `/tmp` tmpfs
- binds the API only to host loopback through Compose
- includes an HTTP health check against `/health`

These controls reduce container privilege but do not replace host, network, orchestrator, image-signing, or secrets-management controls.

## Verify

Check process health:

```bash
curl -i http://127.0.0.1:8000/health
```

Expected body:

```json
{"status":"ok"}
```

Load the existing client credential in the host shell and send the normal authenticated request:

```bash
set -a
source .client.env
set +a
```

The request should preserve the same authentication, policy, Redis rate-limit, PostgreSQL usage, PII, prompt-injection, and tool-authorization behavior as host-run Uvicorn.

Useful logs:

```bash
docker compose logs gateway
docker compose logs --tail=100 gateway
```

## Stop containers

To stop the stack while retaining persistent data:

```bash
docker compose down
```

The named volumes remain intact.
