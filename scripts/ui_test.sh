#!/usr/bin/env bash
# Local UI test harness for delta — no Docker.
#
# Seeds a scratch sqlite DB with the SAME commands the deployment entrypoint
# runs (migrate + init_platform + FULL_INGRESS=1 ingress_all), then sweeps
# every URL of the host with the three-persona smoke_urls command.
#
#   scripts/ui_test.sh                    # in-process sweep (anon/student/staff)
#   scripts/ui_test.sh --live             # + boot runserver, re-probe over HTTP (anon)
#   scripts/ui_test.sh --keep-db          # keep the scratch DB for postmortems
#   scripts/ui_test.sh --filter academy:  # extra args go to smoke_urls
#
# Interpreter: $PYTHON, else the repo venvs, else system python3.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"
elif [ -x "$REPO_ROOT/venv/bin/python" ]; then PY="$REPO_ROOT/venv/bin/python"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"
elif [ -x "$REPO_ROOT/.venv_test/bin/python" ]; then PY="$REPO_ROOT/.venv_test/bin/python"
else PY="$(command -v python3)"
fi

LIVE=0; KEEP_DB=0; SWEEP_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --live) LIVE=1 ;;
        --keep-db) KEEP_DB=1 ;;
        *) SWEEP_ARGS+=("$arg") ;;
    esac
done

SCRATCH="$REPO_ROOT/.ui_test"
mkdir -p "$SCRATCH"

export BUILD_GEO=0                       # plain sqlite, no GDAL
export DB_NAME="$SCRATCH/db.ui_test.sqlite3"
# One key for ALL steps: encrypted rows seeded in one process must stay
# decryptable in the next (settings mints a fresh random key per process).
export FIELD_ENCRYPTION_KEY="${FIELD_ENCRYPTION_KEY:-$("$PY" -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')}"
export ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-ui-test-admin}"
export ADMIN_DISPLAY_NAME="${ADMIN_DISPLAY_NAME:-Delta Admin}"   # links the admin Person
export FULL_INGRESS=1

cleanup() {
    if [ "$KEEP_DB" = "0" ] && [ "${UI_TEST_OK:-0}" = "1" ]; then
        rm -f "$DB_NAME" "$DB_NAME"-shm "$DB_NAME"-wal
    else
        echo "==> scratch DB kept at $DB_NAME"
    fi
}
trap cleanup EXIT

echo "==> scratch DB at $DB_NAME"
rm -f "$DB_NAME" "$DB_NAME"-shm "$DB_NAME"-wal

echo "==> migrate + seed (init_platform, FULL_INGRESS=1 ingress_all)"
"$PY" delta/manage.py migrate --noinput >/dev/null
"$PY" delta/manage.py init_platform --password "$ADMIN_PASSWORD" >/dev/null
INGRESS_OUT="$("$PY" delta/manage.py ingress_all)"
echo "$INGRESS_OUT" | tail -3
# ingress_all reports failures but always exits 0 — enforce zero here.
echo "$INGRESS_OUT" | grep -q "Failed: 0" || {
    echo "==> ingress_all reported failures"; exit 1; }

echo "==> in-process URL sweep"
"$PY" delta/manage.py smoke_urls ${SWEEP_ARGS[@]+"${SWEEP_ARGS[@]}"}

if [ "$LIVE" = "1" ]; then
    echo "==> live-HTTP sweep (runserver on 127.0.0.1:8768, anon persona)"
    "$PY" delta/manage.py runserver 127.0.0.1:8768 --noreload \
        >"$SCRATCH/runserver.log" 2>&1 &
    SERVER_PID=$!
    trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true; wait "$SERVER_PID" 2>/dev/null || true; cleanup' EXIT
    for _ in $(seq 1 20); do
        sleep 1
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8768/" || true)
        [ -n "$code" ] && [ "$code" != "000" ] && break
    done
    if [ -z "${code:-}" ] || [ "$code" = "000" ]; then
        echo "==> runserver failed to boot — see $SCRATCH/runserver.log"; exit 1
    fi
    "$PY" delta/manage.py smoke_urls --base-url http://127.0.0.1:8768 \
        ${SWEEP_ARGS[@]+"${SWEEP_ARGS[@]}"}
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
fi

UI_TEST_OK=1
echo "==> delta UI harness PASSED"
