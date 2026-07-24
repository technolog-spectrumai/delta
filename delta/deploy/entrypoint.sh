#!/bin/sh
set -e

echo "Waiting for Postgres..."
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER"; do
  sleep 1
done

if [ "$RESET" = "1" ]; then
  echo "RESET=1 — wiping and re-seeding database..."
  python manage.py init_platform --password="${ADMIN_PASSWORD:-qwerty123456}" --reset
else
  python manage.py init_platform --password="${ADMIN_PASSWORD:-qwerty123456}"
fi

python manage.py ingress_all
python manage.py collectstatic --noinput

# gervazy self-signed cert: deploy.py normally creates it on the host before
# bringing the stack up, but on a bare server checkout the cryptography package may
# be absent, so generate it here instead. Idempotent — skips when it already exists.
if [ "$SSL_MODE" = "gervazy" ]; then
  python manage.py gen_ssl_cert
fi

echo "Starting server (${SERVER_MODE:-wsgi})..."
exec "$@"
