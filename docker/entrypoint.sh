#!/bin/sh
set -eu

python -m app.policy_cli validate
python -m app.database migrate

exec "$@"
