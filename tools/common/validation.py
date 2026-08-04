"""Validation vocabulary and dotted-path helpers.

Pure functions over plain dicts: no Qt, no file I/O, directly unit-testable.
The rules here are the ones deploy.py's own ``validate_config`` does NOT cover
(it never checks ``server``, service/host compatibility, port collisions or the
feature closure) — everything it *does* cover is delegated, not duplicated, via
deploy_bridge. Two sources of truth would drift; one plus a gap-filler will not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """One problem, addressed to the config path that caused it."""

    path: str
    message: str
    severity: str = ERROR

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.is_error]


def warnings(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if not i.is_error]


def issues_for(issues: Iterable[Issue], *prefixes: str) -> list[Issue]:
    """The subset a given tab owns, so each renders only its own errors."""
    return [i for i in issues if any(i.path.startswith(p) for p in prefixes)]


def get_path(data: dict, path: str) -> Any:
    """``get_path(cfg, "ssl.mode")`` → the value, or None if any hop is absent."""
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(data: dict, path: str, value: Any) -> None:
    """Set a dotted path, creating intermediate dicts as needed."""
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        existing = node.get(part)
        if not isinstance(existing, dict):
            existing = {}
            node[part] = existing
        node = existing
    node[parts[-1]] = value


def drop_path(data: dict, path: str) -> None:
    """Remove a dotted path, then prune any dicts it left empty.

    This is what makes "omit what the user did not set" work: an unchecked
    option must not leave ``services: {}`` behind, because deploy.py's defaults
    only apply to keys that are genuinely absent.
    """
    parts = path.split(".")
    chain: list[tuple[dict, str]] = []
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        chain.append((node, part))
        node = node[part]
    if isinstance(node, dict):
        node.pop(parts[-1], None)
    for parent, key in reversed(chain):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)


# ---------------------------------------------------------------------------
# Host-agnostic rules that deploy.py's validate_config does not apply
# ---------------------------------------------------------------------------

SERVER_MODES = ("asgi", "wsgi")

# Published host ports already claimed across the monorepo and its siblings.
# See the allocation table in the repo-root README; delta holds 8082/8443 and
# the clean-env gates hold 8765/8769.
# Ports another stack on the same machine already claims. delta's own 8082/8443
# are deliberately ABSENT: this is delta's copy of the table, and a host must not
# warn about the ports its own shipped profiles use. The rest stay listed because
# a developer commonly runs a portal host beside delta.
RESERVED_PORTS: dict[int, str] = {
    80: "zenobia / aurelian_server / studio_server (http)",
    443: "every https profile",
    8080: "zenobia_full_dev",
    8081: "zenobia_mini",
    8083: "aurelian_local",
    8084: "studio_local",
    8444: "aurelian_local",
    8445: "studio_local",
    8767: "delta clean-env gate boot smoke",
    8765: "zenobia clean-env gate boot smoke",
    8769: "aurelian clean-env gate boot smoke",
    8770: "studio clean-env gate boot smoke",
}

# Sidecars whose host port deploy.py hardcodes — two stacks that both enable
# one of these cannot run at the same time, and there is no knob to fix it.
FIXED_SIDECAR_PORTS = {
    "prometheus": 9091,
    "grafana": 3000,
    "loki": 3100,
    "gitea": 3300,
}


def check_common(cfg: dict) -> list[Issue]:
    """Rules that hold for any host."""
    found: list[Issue] = []

    name = get_path(cfg, "deployment.name")
    project_dir = get_path(cfg, "deployment.project_dir")
    if name and not str(name).replace("_", "").replace("-", "").isalnum():
        found.append(Issue(
            "deployment.name",
            "must be alphanumeric with - or _ (it becomes the compose project "
            "name, a network name and a service name)",
        ))

    # deployment.name is the compose project name, and compose named volumes
    # (postgres_data, media_volume, static_volume) are scoped to it. A profile
    # that borrows another host's name therefore adopts that host's DATABASE
    # and media — and `up --service` runs `down` on it first. Silent, and very
    # hard to read back from the symptom.
    if name and project_dir and str(name).split("_")[0] != str(project_dir):
        found.append(Issue(
            "deployment.name",
            f"names the {str(name).split('_')[0]!r} stack but project_dir is "
            f"{project_dir!r} — compose volumes are scoped to the name, so this "
            f"profile would share {str(name).split('_')[0]!r}'s database and "
            "media, and bringing it up would tear that stack down",
        ))

    server = get_path(cfg, "server")
    if server is not None and server not in SERVER_MODES:
        found.append(Issue(
            "server", f"must be one of: {', '.join(SERVER_MODES)}"
        ))

    for key in ("http_port", "https_port"):
        port = get_path(cfg, f"nginx.{key}")
        if port is None:
            continue
        if not isinstance(port, int) or not (1 <= port <= 65535):
            found.append(Issue(f"nginx.{key}", "must be a port number (1-65535)"))
            continue
        owner = RESERVED_PORTS.get(port)
        if owner:
            found.append(Issue(
                f"nginx.{key}",
                f"port {port} is already used by {owner} — the two stacks "
                "cannot run at the same time",
                WARNING,
            ))

    http_port = get_path(cfg, "nginx.http_port")
    https_port = get_path(cfg, "nginx.https_port")
    if http_port and https_port and http_port == https_port:
        found.append(Issue("nginx.https_port", "must differ from nginx.http_port"))

    ssl_mode = get_path(cfg, "ssl.mode")
    if ssl_mode != "none" and not https_port:
        found.append(Issue(
            "nginx.https_port",
            "no https port set, so no TLS listener is published — but the "
            "http→https redirect is still generated. Set it, or use "
            "ssl.mode: none.",
            WARNING,
        ))

    services = cfg.get("services") or {}
    for service, port in FIXED_SIDECAR_PORTS.items():
        if services.get(service):
            found.append(Issue(
                f"services.{service}",
                f"publishes host port {port}, which deploy.py hardcodes — only "
                "one stack on this machine may enable it",
                WARNING,
            ))

    return found


def check_flags(env: dict) -> list[Issue]:
    """The BUILD_* value convention deploy.py cannot see.

    ``toto.features.flag`` treats *only* the exact string "1" as true, so a
    YAML boolean silently reads as false (``str(True) == "True"``). Generated
    files always quote, but a preset edited by hand might not.
    """
    found: list[Issue] = []
    for key, value in env.items():
        if not (key.startswith("BUILD_") or key.startswith("INSTALL_")):
            continue
        if isinstance(value, bool) or str(value) not in ("0", "1"):
            found.append(Issue(
                f"env.{key}",
                f'must be the string "0" or "1" (got {value!r}); only "1" is '
                "true to toto.features",
            ))
    return found
