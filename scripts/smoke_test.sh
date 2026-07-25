#!/usr/bin/env bash
# Smoke test for a RUNNING delta stack (after ./deploy_local.sh).
#
# Probes the nginx front door: the web container must be healthy, every route
# must answer below HTTP 500, and the static pipeline must serve a manifest
# asset (200). Anonymous requests may legitimately redirect (301/302) — only
# server errors and dead sockets fail the smoke.
#
# Usage: scripts/smoke_test.sh [base-url]    (default https://127.0.0.1:8443,
#                                             the delta_local.yaml front door)
set -euo pipefail

BASE="${1:-https://127.0.0.1:8443}"
FAIL=0

probe() {
    local path="$1" want="${2:-}"
    local code
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "$BASE$path" || true)
    if [ -z "$code" ] || [ "$code" = "000" ] || [ "$code" -ge 500 ]; then
        echo "    FAIL $path (http=$code)"; FAIL=1; return 0
    fi
    if [ -n "$want" ] && [ "$code" != "$want" ]; then
        echo "    FAIL $path (http=$code, want $want)"; FAIL=1; return 0
    fi
    echo "    ok   $path ($code)"
}

echo "==> web container health"
state=$(docker inspect -f '{{.State.Health.Status}}' delta-web_delta-1 2>/dev/null || echo missing)
if [ "$state" != "healthy" ]; then
    echo "    FAIL web container is '$state' (expected healthy)"; FAIL=1
else
    echo "    ok   web container healthy"
fi

if [ $# -eq 0 ]; then
    echo "==> http -> https redirect"
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8082/ || true)
    if [ "$code" = "301" ]; then
        echo "    ok   :8082 redirects ($code)"
    else
        echo "    FAIL :8082 (http=$code, want 301)"; FAIL=1
    fi
fi

echo "==> HTTP probes against $BASE"
probe /                                   # dashboard (may redirect to login)
probe /sso/login/ 200                     # login page must render
probe /static/admin/css/base.css 200      # collectstatic manifest asset
probe /nauka/                             # academy
probe /nauka/paths/                       # personal learning paths
probe /zadania/                           # quizzes
probe /notatki/                           # palimpsest
probe /biblioteka/books/                  # library (its mount root has no view)
probe /subskrypcje/                       # subscriptions

if [ "$FAIL" -ne 0 ]; then
    echo "==> delta smoke test FAILED"
    exit 1
fi
echo "==> delta smoke test PASSED"
