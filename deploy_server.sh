#!/usr/bin/env bash
# Deploy delta to a remote host (rsync + docker compose), driven by the target:
# block in delta_server.yaml.
#
#   ./deploy_server.sh provision   # first-time host setup (docker, TLS cert)
#   ./deploy_server.sh             # -> deploy.py delta_server.yaml push
#   ./deploy_server.sh renew       # Let's Encrypt renewal (no-op unless near expiry)
#
# Override the config with:  CONFIG=delta/deploy/configs/other.yaml ./deploy_server.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${CONFIG:-$ROOT/delta/deploy/configs/delta_server.yaml}"
CMD="${1:-push}"

if [ -x "$ROOT/venv/bin/python" ]; then
  PY="$ROOT/venv/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv_test/bin/python" ]; then
  PY="$ROOT/.venv_test/bin/python"
else
  PY="$(command -v python3)"
fi

exec "$PY" "$ROOT/delta/scripts/deploy.py" "$CONFIG" "$CMD" "${@:2}"
