#!/usr/bin/env bash
# Deploy the delta_local profile on this machine (local docker compose stack:
# Django/WSGI + Postgres + Redis + nginx at https://localhost:8443).
#
#   ./deploy_local.sh              # -> deploy.py delta_local.yaml up --smart
#   ./deploy_local.sh <command>    # any deploy.py command: down/restart/logs/config/validate/...
#   ./deploy_local.sh logs -f      # extra args are passed through
#
# Smart image reuse: up/restart run with --smart by default — the image is
# rebuilt only when its build inputs changed (Dockerfile, entrypoint,
# requirements, download_vendor.py, data/, toto library source). Force a full
# rebuild with:  BUILD=1 ./deploy_local.sh
#
# toto resolution: deploy.py uses the in-repo vendored tree at vendor/toto_libs
# (or TOTO_SRC env / toto_src config key) and stages the toto wheel into dist/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${CONFIG:-$ROOT/delta/deploy/configs/delta_local.yaml}"

# If the first arg is an option flag (e.g. --dev), keep the default command 'up'
# and pass every arg through; otherwise the first arg is the deploy.py command.
if [ $# -gt 0 ] && [ "${1#-}" != "$1" ]; then
  CMD="up"; ARGS=("$@")
else
  CMD="${1:-up}"; ARGS=("${@:2}")
fi

# Rebuild only when image build inputs changed; BUILD=1 forces a rebuild.
EXTRA=()
case "$CMD" in
  up|restart) [ "${BUILD:-0}" = "1" ] || EXTRA+=(--smart) ;;
esac

# Interpreter: prefer a project venv, fall back to system python3.
if [ -x "$ROOT/venv/bin/python" ]; then
  PY="$ROOT/venv/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv_test/bin/python" ]; then
  PY="$ROOT/.venv_test/bin/python"
else
  PY="$(command -v python3)"
fi

exec "$PY" "$ROOT/delta/scripts/deploy.py" "$CONFIG" "$CMD" ${EXTRA[@]+"${EXTRA[@]}"} ${ARGS[@]+"${ARGS[@]}"}
