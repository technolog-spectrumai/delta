#!/usr/bin/env python
"""Django management entry point for delta (local development).

delta is a small WSGI e-learning host. Env vars control everything — no YAML is
needed here. Default: DB_ENGINE=spatialite (no postgres required locally), using a
delta-specific sqlite file.

toto normally runs straight from the vendored subtree at vendor/toto_libs;
TOTO_SRC overrides it (set-but-missing = explicitly disabled, so the clean-env
gate can prove wheel-only operation). In the Docker image neither exists and
the installed wheel provides the namespace.

To customise without exporting env vars, create .env.local in the repo root:
    DB_ENGINE=postgis
    DB_PASSWORD=secret
"""
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # delta/delta  (project dir)
_REPO_ROOT = _HERE.parent                          # delta        (repo root)

# Vendored tree by default; TOTO_SRC overrides — each package's namespace
# portion goes on sys.path.
_toto_src = os.environ.get("TOTO_SRC")
if _toto_src is None:
    _vendor = _REPO_ROOT / "vendor" / "toto_libs"
    if (_vendor / "packages").is_dir():
        _toto_src = str(_vendor)
if _toto_src and Path(_toto_src).exists():
    _portions = sorted((Path(_toto_src) / "packages").glob("*/src"))
    for _portion in _portions or [Path(_toto_src)]:
        sys.path.insert(0, str(_portion))
sys.path.insert(0, str(_HERE))

_env_local = _REPO_ROOT / ".env.local"
if _env_local.exists():
    for _line in _env_local.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delta.settings")

if __name__ == "__main__":
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Couldn't import Django. Is your virtualenv active?") from exc
    execute_from_command_line(sys.argv)
