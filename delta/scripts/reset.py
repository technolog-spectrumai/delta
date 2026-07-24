#!/usr/bin/env python3
"""Reset local faros dev environment: wipe sqlite DB, run migrations, seed data.

faros is the minimal real-time server (ASGI + websockets, no Neo4j / labs / Celery),
so unlike portal's reset there are no BUILD_* flags to toggle — faros always runs the
same fixed app set. It keeps its own sqlite file (db.faros.sqlite3) so it never
clobbers portal's local dev DB.

Run from the repo root:
    python faros/scripts/reset.py --serve
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/ -> faros/ -> repo root
FAROS_DIR = ROOT / "faros"
DB_PATH = ROOT / "db.faros.sqlite3"
RUN_DIR = ROOT / "run"

# Platform identity for this instance.
_PLATFORM_NAME = "Faros"
_PLATFORM_DOMAIN = "localhost:8003"
_PLATFORM_AUTHOR = ""
_PLATFORM_LOGO_PATH = os.path.join("..", "data", "img", "platform_logo.png")
_FAROS_BASE_URL = "http://localhost:8003"  # local dev server URL


# Env vars for local dev (spatialite, no postgres)
DEV_ENV = {
    "DEBUG": "1",
    "FULL_INGRESS": "1",
    "LOGIN_RETRY_COOLDOWN_SECONDS": "0",
    "DJANGO_SETTINGS_MODULE": "faros.settings",
    # Dev-only vault password for the SSO signing key
    "SSO_VAULT_PASSWORD": "sso-dev-vault-password",
    "PLATFORM_NAME": _PLATFORM_NAME,
    "PLATFORM_DOMAIN": _PLATFORM_DOMAIN,
    "PLATFORM_AUTHOR": _PLATFORM_AUTHOR,
    "PLATFORM_LOGO_PATH": _PLATFORM_LOGO_PATH,
}


def _make_env():
    return dict(DEV_ENV)


def run(cmd, cwd=None, env=None, allow_fail=False):
    cwd = cwd or FAROS_DIR
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    print()
    print("=" * 80)
    print(f"▶ cwd: {cwd}")
    print(f"▶ cmd: {' '.join(cmd)}")
    print("=" * 80)

    result = subprocess.run(cmd, cwd=cwd, env=merged_env, text=True)

    if result.returncode != 0 and not allow_fail:
        print(f"\n❌ Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    return result.returncode


def remove_db():
    removed = False
    # Drop the main file plus its WAL/SHM siblings so no orphan journal survives.
    for path in (DB_PATH, Path(f"{DB_PATH}-wal"), Path(f"{DB_PATH}-shm")):
        if path.exists():
            print(f"🗑 Removing database: {path}")
            path.unlink()
            removed = True
    if not removed:
        print(f"ℹ Database does not exist, skipping: {DB_PATH}")


def purge_migrations():
    """Delete all migration files (except __init__.py). Breaks compatibility — use at your own risk."""
    toto_src = ROOT / "toto" / "toto"
    deleted = 0
    for mig_file in toto_src.rglob("migrations/*.py"):
        if mig_file.name == "__init__.py":
            continue
        print(f"  🗑 {mig_file.relative_to(ROOT)}")
        mig_file.unlink()
        deleted += 1
    print(f"⚠ Purged {deleted} migration file(s). Run makemigrations to regenerate.")


def ensure_migrations():
    """Run makemigrations for any app whose migrations/ dir has no files beyond __init__.py."""
    toto_src = ROOT / "toto" / "toto"
    apps_needing_migrations = []
    for migrations_dir in toto_src.rglob("migrations"):
        if not migrations_dir.is_dir():
            continue
        has_migration = any(
            f.suffix == ".py" and f.name != "__init__.py"
            for f in migrations_dir.iterdir()
        )
        if not has_migration:
            app_dir = migrations_dir.parent
            apps_needing_migrations.append(app_dir.name)

    if apps_needing_migrations:
        print(f"\n⚙ Missing migrations detected for: {', '.join(apps_needing_migrations)}")
        run([sys.executable, "manage.py", "makemigrations"] + apps_needing_migrations, env=_make_env())


def faros_setup():
    print("\n🚀 Setting up faros...")
    env = _make_env()
    ensure_migrations()
    run([sys.executable, "manage.py", "init_platform", "--reset"], env=env)
    run([sys.executable, "manage.py", "ingress_all"], env=env)
    _provision_sso(env)


def _provision_sso(env):
    print("\n🔑 Provisioning SSO...")
    run([
        sys.executable, "manage.py", "create_sso_signing_key",
        "--key-id", "sso-dev",
        "--vault-password", env["SSO_VAULT_PASSWORD"],
    ], env=env)

    # Persist the dev vault password so bare `manage.py runserver` sessions
    # (no env) can unlock SSO via the settings run/sso_*.json fallback.
    RUN_DIR.mkdir(exist_ok=True)
    bundle_path = RUN_DIR / "sso_local.json"
    bundle_path.write_text(json.dumps({
        "schema_version": 1,
        "bundle_type": "local_dev_secrets",
        "vault_password": env["SSO_VAULT_PASSWORD"],
    }, indent=2))
    print(f"  vault-password bundle → {bundle_path}")


def download_vendor():
    print("\n⬇ Downloading vendor assets...")
    # download_vendor.py is local to faros/scripts.
    vendor_script = ROOT / "faros" / "scripts" / "download_vendor.py"
    result = subprocess.run(
        [sys.executable, str(vendor_script)],
        cwd=ROOT,
        text=True,
    )
    if result.returncode != 0:
        print(f"\n❌ download_vendor.py failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def collect_static():
    print("\n📦 Collecting static files...")
    run([sys.executable, "manage.py", "collectstatic", "--noinput"], env=_make_env())


def serve_faros():
    print(f"\n🌐 Starting faros dev server on {_FAROS_BASE_URL} ...")
    run([sys.executable, "manage.py", "runserver", "8003"], env=_make_env())


def run_tests():
    print("\n🧪 Running websocket integration test...")
    env = _make_env()
    env["CHANNELS_TEST_DB"] = "1"

    run(
        [sys.executable, "manage.py", "test",
         "toto.forum.tests.test_consumers"],
        env=env,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Reset local db, seed faros demo data, optionally run tests."
    )
    parser.add_argument(
        "--skip-db-remove",
        action="store_true",
        help="Do not remove db.faros.sqlite3 first.",
    )
    parser.add_argument(
        "--skip-faros",
        action="store_true",
        help="Skip faros init_platform and ingress_all.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start runserver 8003 with dev env after setup.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run websocket integration test after setup.",
    )
    parser.add_argument(
        "--purge-migrations",
        action="store_true",
        help="⚠ Delete all migration files (except __init__.py) before setup. "
             "Breaks migration history — use only when you want a clean slate. Your risk.",
    )

    args = parser.parse_args()

    if args.purge_migrations:
        purge_migrations()

    if not args.skip_db_remove:
        remove_db()

    if not args.skip_faros:
        faros_setup()
        download_vendor()
        collect_static()

    if args.test:
        run_tests()

    if args.serve:
        serve_faros()
    else:
        print("\n✅ Done.")


if __name__ == "__main__":
    main()
