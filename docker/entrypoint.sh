#!/bin/sh
set -eu

python -m app.policy_cli validate

case "${SAG_RUN_MIGRATIONS:-true}" in
  true|1|yes|on)
    python -m app.database migrate
    ;;
  false|0|no|off)
    ;;
  *)
    echo "ERROR startup reason=invalid_SAG_RUN_MIGRATIONS" >&2
    exit 2
    ;;
esac

exec "$@"
