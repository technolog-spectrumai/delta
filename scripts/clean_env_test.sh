#!/usr/bin/env bash
# Clean-environment gate for the delta repo.
#
# Proves, in a fresh venv, that the delta host works against the toto WHEEL
# alone — no toto source checkout on sys.path:
#   1. host requirements install
#   2. the toto wheel builds from the vendored tree and installs
#   3. deploy.py validate + config succeed for both delta profiles
#   4. the existing deploy.py unit tests pass
#   5. django check passes
#   6. migrate + collectstatic succeed (throwaway sqlite DB)
#
# Usage: scripts/clean_env_test.sh   [TOTO_SRC=/path overrides the default
#                                     in-repo vendor/toto_libs tree]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOTO_CHECKOUT="${TOTO_SRC:-$REPO_ROOT/vendor/toto_libs}"
VENV="$REPO_ROOT/.venv_test"
PYTHON="${PYTHON:-python3}"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"

# The gate runs GIS-off (delta's local profile): plain sqlite + the
# locations migrations_nogis graph — no GDAL/GEOS needed on the host.
export BUILD_GEO=0
# Gate-owned scratch DB/media: never touch the developer's db.delta.sqlite3
# or the docker-owned media/ dir. One encryption key for every step, or rows
# seeded in one process could not be decrypted in the next.
export DB_NAME="$VENV/db.gate.sqlite3"
export MEDIA_ROOT="$VENV/media"
export FIELD_ENCRYPTION_KEY="${FIELD_ENCRYPTION_KEY:-$("$PYTHON" -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-gate-admin}"
export ADMIN_DISPLAY_NAME="${ADMIN_DISPLAY_NAME:-Delta Admin}"
export FULL_INGRESS=1

cd "$REPO_ROOT"

echo "==> fresh venv at $VENV"
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
"$PIP" install --quiet --upgrade pip

echo "==> host requirements"
"$PIP" install --quiet -r requirements.txt

echo "==> build + install the pinned toto suite (from $TOTO_CHECKOUT)"
rm -rf "$REPO_ROOT/dist"
mkdir -p "$REPO_ROOT/dist"
PINS="$(grep -E '^toto-[a-z0-9-]+==' "$REPO_ROOT/requirements.toto.txt")"
for PIN in $PINS; do
    # Clear stale build/lib first: setuptools reuses it and would re-ship files
    # already deleted from the checkout (see vendor/toto_libs/README.md).
    rm -rf "$TOTO_CHECKOUT/packages/${PIN%%==*}/build"
    "$PIP" wheel --quiet --no-deps --wheel-dir "$REPO_ROOT/dist" "$TOTO_CHECKOUT/packages/${PIN%%==*}"
done
# --no-index: only the wheels just built may satisfy the pins (as in the image).
"$PIP" install --quiet --no-index --find-links "$REPO_ROOT/dist" -r "$REPO_ROOT/requirements.toto.txt"
# -I (isolated): keep the invoking cwd off sys.path so no source tree shadows
# the installed wheels.
"$PY" -I - $PINS <<'PYCHECK'
import importlib.metadata as md
import sys

from toto.versioning import check_runtime_coherence

for pin in sys.argv[1:]:
    name, expected = pin.split("==")
    found = md.version(name)
    assert found == expected, f"{name} is {found}, expected {expected}"
    print(f"    {name} {found}")
check_runtime_coherence()
print("    suite coherent")
PYCHECK

echo "==> host-carried toto portion (delta owns these apps outright)"
# delta/toto/* is a PEP 420 portion of the same toto.* namespace as the
# installed wheel. It must resolve from the project dir alone — the same
# dir DJANGO_SETTINGS_MODULE=delta.settings already needs.
(cd "$REPO_ROOT/delta" && "$PY" -c '
import sys; sys.path.insert(0, ".")
import toto, toto.academy, toto.quizzes, toto.competence, toto.palimpsest, toto.library, toto.subscriptions, toto.core
assert toto.__file__ is None, "toto must stay a namespace package"
assert "/delta/toto/" in toto.academy.__file__, toto.academy.__file__
assert "site-packages" in toto.core.__file__, toto.core.__file__
print("    the six education apps load from the repo, core from the wheel")
')

# From here on: prove wheel-only operation. TOTO_SRC pointing at a missing dir
# disables deploy.py's and manage.py's vendor-tree default without touching
# sys.path.
export TOTO_SRC="$REPO_ROOT/.no-such-toto-src"

echo "==> deploy.py validate + config for every profile"
for c in delta/deploy/configs/*.yaml; do
    # delta_server.yaml ships as an unedited template until a real server
    # exists; validate it once the placeholder host is filled in.
    if grep -q 'your-server.example.com' "$c"; then
        echo "    $(basename "$c") skipped (unedited server template)"
        continue
    fi
    "$PY" delta/scripts/deploy.py "$c" validate >/dev/null
    "$PY" delta/scripts/deploy.py "$c" config >/dev/null
    echo "    $(basename "$c") OK"
done

echo "==> deploy.py unit tests"
"$PY" delta/scripts/test_deploy.py 2>&1 | tail -1

echo "==> django check"
"$PY" delta/manage.py check >/dev/null
echo "    delta OK"

echo "==> migration (throwaway sqlite DB)"
rm -f "$DB_NAME" "$DB_NAME"-shm "$DB_NAME"-wal
mkdir -p "$MEDIA_ROOT"
"$PY" delta/manage.py migrate --noinput >/dev/null
echo "    delta migrate OK"

echo "==> app tests (academy + panel + the memo/cyprian/primula editors)"
# toto is a PEP 420 namespace, so the modules are named explicitly —
# `manage.py test toto.academy` discovers nothing. memo's and primula's suites
# need their own settings modules (each tests its app standalone); academy's
# includes the 0005→0006 deck-conversion migration test. backoffice.tests also
# carries SheetsGateTests — the teacher_required wrap on /primula/ is delta's,
# so the standalone primula suite never sees it.
"$PY" delta/manage.py test --noinput \
    toto.academy.tests toto.backoffice.tests \
    toto.cyprian.tests.test_format toto.cyprian.tests.test_views \
    toto.cyprian.tests.test_sanitize toto.cyprian.tests.test_tiptap \
    toto.cyprian.tests.test_widget toto.quizzes.tests toto.palimpsest.tests 2>&1 | tail -2
(cd /tmp && DJANGO_SETTINGS_MODULE=toto.memo.testing.settings \
    "$PY" -m django test toto.memo.tests 2>&1 | tail -2)
(cd /tmp && DJANGO_SETTINGS_MODULE=toto.primula.testing.settings \
    "$PY" -m django test toto.primula.tests 2>&1 | tail -2)

echo "==> seeded three-persona URL sweep"
"$PY" delta/manage.py init_platform --password "$ADMIN_PASSWORD" >/dev/null
INGRESS_OUT="$("$PY" delta/manage.py ingress_all)"
# ingress_all reports failures but always exits 0 — enforce zero here.
echo "$INGRESS_OUT" | grep -q "Failed: 0" || {
    echo "$INGRESS_OUT" | tail -5; echo "    ingress_all reported failures"; exit 1; }
"$PY" delta/manage.py smoke_urls | tail -6

echo "==> collectstatic (ResilientManifestStaticFilesStorage, scratch root)"
# Collect into a throwaway STATIC_ROOT so the gate never touches the real
# delta/staticfiles dir (owned by a running docker stack's bind mount).
cat > "$VENV/gate_settings.py" <<EOF
from delta.settings import *  # noqa: F401,F403
STATIC_ROOT = "$VENV/static_collect"
EOF
rm -rf "$VENV/static_collect"
PYTHONPATH="$VENV" DJANGO_SETTINGS_MODULE=gate_settings \
    "$PY" delta/manage.py collectstatic --noinput >/dev/null
echo "    delta collectstatic OK"

echo "==> server boot smoke (wheel-only)"
_boot_smoke() {
    local label="$1" managepy="$2" port="$3"
    "$PY" "$managepy" runserver "127.0.0.1:$port" --noreload \
        >"$REPO_ROOT/.venv_test/boot_$label.log" 2>&1 &
    local pid=$!
    local code=""
    for _ in $(seq 1 20); do
        sleep 1
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" || true)
        [ -n "$code" ] && [ "$code" != "000" ] && break
    done
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
    if [ -z "$code" ] || [ "$code" = "000" ] || [ "$code" -ge 500 ]; then
        echo "    $label boot smoke FAILED (http=$code) — see .venv_test/boot_$label.log"
        return 1
    fi
    echo "    $label boot OK (http $code)"
}
_boot_smoke delta delta/manage.py 8767

echo "==> cleanup of generated artifacts"
rm -f "$DB_NAME" "$DB_NAME"-shm "$DB_NAME"-wal
rm -rf "$VENV/static_collect" "$MEDIA_ROOT"

echo "==> delta clean-env gate PASSED"
