"""Borrow the real deploy tool's validator instead of reimplementing it.

``scripts/deploy.py`` is the authority on what a config may contain. Importing it
means the builder can never disagree with what a deploy will accept — the
alternative, restating its ~14 rules here, guarantees drift the first time
someone edits one of them.

Two import-time facts drive the shape of this module:

* deploy.py computes ``ROOT_DIR = _host_root()`` at import and ``sys.exit``s if
  it cannot, so ``HOST_ROOT`` must be in the environment *before* the import.
* it also puts the vendored ``toto.*`` namespace portions on ``sys.path``, which
  is what makes ``toto.features`` importable for the closure check below.

``validate_config`` itself is a pure function over the dict — the path argument
only decorates messages. So the builder may save anywhere, unlike ``deploy.py``
on the command line, which additionally refuses a config outside its host root.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from validation import ERROR, WARNING, Issue

_MODULE_CACHE: dict[str, Any] = {}


def load_deploy(host_root: Path) -> Any:
    """Import ``scripts/deploy.py`` bound to one host.

    Cached per host root: deploy.py's constants freeze at import, so a second
    import for the same host is pointless and one for a *different* host would
    silently keep the first host's ROOT_DIR. A builder only ever drives one.
    """
    key = str(host_root)
    if key in _MODULE_CACHE:
        return _MODULE_CACHE[key]

    # Standalone repo first (delta keeps its own fork at delta/scripts/deploy.py),
    # monorepo layout second — the same order deployer.shim() already uses, so the
    # GUI and the tool it drives can never resolve to different files.
    candidates = [
        host_root / host_root.name / "scripts" / "deploy.py",
        host_root.parent / "scripts" / "deploy.py",
    ]
    shared = next((c for c in candidates if c.is_file()), None)
    if shared is None:
        raise FileNotFoundError(
            "deploy.py not found — looked in "
            + " and ".join(str(c) for c in candidates)
        )

    os.environ["HOST_ROOT"] = str(host_root)
    spec = importlib.util.spec_from_file_location("_builder_deploy", shared)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {shared}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so dataclasses/pickling inside it resolve, and so a
    # failed exec does not leave a half-built module importable.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    _MODULE_CACHE[key] = module
    return module


def validate(host_root: Path, cfg: dict, target: Path) -> list[Issue]:
    """Run deploy.py's own validator and translate its strings into Issues.

    deploy.py returns flat message strings; we map each back onto the config
    path it names so the right field lights up. Anything unrecognised is
    attributed to the section it mentions, or to the document as a whole.
    """
    deploy = load_deploy(host_root)
    try:
        messages = deploy.validate_config(cfg, target)
    except SystemExit as exc:  # _die() inside a helper — surface, never exit
        return [Issue("", f"deploy.py refused this config: {exc}", ERROR)]

    issues: list[Issue] = []
    for message in messages:
        issues.append(Issue(_attribute(message), message, ERROR))
    return issues


def _attribute(message: str) -> str:
    """Best-effort mapping from a deploy.py message to a config path."""
    # Most messages lead with the offending key, e.g.
    # "ssl.email is required when ssl.mode is letsencrypt".
    head = message.split(" ", 1)[0].rstrip(":")
    if "." in head and not head.endswith("."):
        return head
    for section in ("deployment", "ssl", "target", "services", "nginx", "tor",
                    "tailscale", "reachability", "env", "admin", "cert"):
        if section in message:
            return section
    return ""


def feature_closure(host_root: Path, env: dict) -> tuple[dict, list[Issue]]:
    """Resolve BUILD_* flags the way the image build will.

    Worth doing for two reasons. It reveals the closure to the user (enabling
    BUILD_WEATHER silently turns on workflows, and BUILD_SKETCH turns on ASGI),
    and it catches
    the one contradiction ``deploy.py validate`` cannot: FeatureConfigError is
    raised from ``build_compose``, so ``validate`` passes a config that ``config``
    then dies on with a bare traceback.
    """
    load_deploy(host_root)  # puts the vendored toto packages on sys.path
    try:
        from toto.features import FeatureConfigError, resolve_features
    except ImportError as exc:
        return {}, [Issue("", f"toto.features is not importable: {exc}", WARNING)]

    lookup = {k: str(v) for k, v in (env or {}).items()}
    try:
        features = resolve_features(lookup.get)
    except FeatureConfigError as exc:
        return {}, [Issue("env.BUILD_GEO", str(exc), ERROR)]

    resolved = {
        name: getattr(features, name)
        for name in dir(features)
        if not name.startswith("_") and isinstance(getattr(features, name), bool)
    }
    return resolved, []
