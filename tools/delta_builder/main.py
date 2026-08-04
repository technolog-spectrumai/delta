#!/usr/bin/env python3
"""Delta Builder — generate a deploy config for the delta host.

    tools/delta_builder/venv/bin/python tools/delta_builder/main.py

Same shape as the portal hosts' builders, with one difference that matters: delta
is a standalone repo, so the shared library is VENDORED at tools/common rather
than borrowed from a monorepo sibling. See tools/common/README.md for what was
patched on the way in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_HOST_ROOT = _HERE.parents[1]                 # tools/<builder> -> repo root
_COMMON = _HOST_ROOT / "tools" / "common"     # vendored, not ../tools/common

if not _COMMON.is_dir():
    sys.exit(f"vendored builder library not found at {_COMMON}")

# Before importing anything shared: deploy_bridge reads HOST_ROOT at import time.
os.environ.setdefault("HOST_ROOT", str(_HOST_ROOT))
sys.path.insert(0, str(_COMMON))
sys.path.insert(0, str(_HERE))

from app import run  # noqa: E402
from spec import build_spec  # noqa: E402


def main() -> int:
    return run(build_spec(_HOST_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
