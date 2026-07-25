#!/usr/bin/env python3
"""
Unified deployment script for toto stacks.

Usage:
    python faros/scripts/deploy.py <config.yaml> [command] [options]

Commands:
    up          Build and start the stack (default)
    down        Stop and remove containers
    restart     Down + up
    stop        Stop the named service
    logs        Follow logs
    config      Print the generated docker-compose YAML (dry run)
    env         Print the generated .env content (dry run)
    nginx       Print the generated nginx.conf (dry run)
    tor         Print the generated torrc (dry run)
    onion       Print the .onion hostname (stack must be running)
    validate    Validate the config file only
    provision   Install Docker + Tailscale on a remote target host
    push        Rsync project to remote host and (re)start the stack

Options:
    --debug / --no-debug            Toggle DEBUG in .env
    --reset-db / --no-reset-db      Toggle RESET (wipe + seed DB on startup)
    --no-build                      Reuse existing docker images (skip rebuild + staging)
    --smart                         Rebuild only when image build inputs changed
    --fake-data / --no-fake-data    Toggle FULL_INGRESS (demo data)
    --admin-password PASSWORD       Set ADMIN_PASSWORD in .env
    -d / --detach                   Run detached (background)
    --service                       Run as a named Compose project (stable name for restarts)

Target config (YAML):
    target:
      type: local          # local | ec2 | ovh | device
      host: 1.2.3.4        # SSH host (required for non-local)
      user: ubuntu         # SSH user (default: ubuntu)
      key: ~/.ssh/id_rsa   # path to SSH private key
      remote_dir: /srv/toto

Tailscale config (YAML):
    tailscale:
      enabled: true
      hostname: portal-prod       # advertised Tailscale hostname
      auth_key: tskey-auth-...    # OR set env var named in authkey_env
      authkey_env: TS_AUTHKEY     # env var holding the auth key (default)

Server mode (YAML):
    server: wsgi           # wsgi | asgi  (default: asgi when a WebSocket feature is on, else wsgi)

SSL config (YAML):
    ssl:
      mode: gervazy        # gervazy | tailscale | letsencrypt | none
      email: ops@ex.com    # letsencrypt only
      staging: false       # letsencrypt: use staging CA
      domain: portal.ex.com  # letsencrypt domain (falls back to env.PLATFORM_DOMAIN)

Examples:
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml up --no-reset-db -d
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml logs
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml validate
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml nginx
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml provision
    python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml push
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required.  Run:  pip install PyYAML", file=sys.stderr)
    sys.exit(1)

# deploy.py lives in faros/scripts/ but operates on the whole repo, so ROOT_DIR
# is the repo root: faros/scripts/ -> faros/ -> repo root (three levels up).
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
COMPOSE_DIR = ROOT_DIR / ".compose"


def _cert_dir(cfg: dict) -> Path:
    """Per-project cert dir (<project_dir>/cert). portal and faros each keep their
    own self-signed cert instead of sharing/overwriting one at the repo root."""
    return ROOT_DIR / cfg["deployment"]["project_dir"] / "cert"

# toto is a suite of installed packages sharing the toto.* namespace. The
# repo carries its own copy as a git subtree at vendor/toto_libs (see
# full_secession.md); TOTO_SRC (env) overrides it — set-but-missing means
# "explicitly disabled" (the clean-env gate relies on that to prove wheel-only
# operation). Which version this host requires is declared in
# requirements.toto.txt and enforced at build time (stage_toto_wheels).
TOTO_MANIFEST = ROOT_DIR / "requirements.toto.txt"
VENDOR_TOTO = ROOT_DIR / "vendor" / "toto_libs"


def _is_suite_checkout(p: Path) -> bool:
    return (p / "VERSION").is_file() and (p / "packages").is_dir()


def _toto_src() -> Path | None:
    env = os.environ.get("TOTO_SRC")
    if env:
        p = Path(env).expanduser().resolve()
        return p if p.exists() else None
    return VENDOR_TOTO if _is_suite_checkout(VENDOR_TOTO) else None


TOTO_SRC = _toto_src()
if TOTO_SRC is not None:
    # Every package holds one portion of the toto.* namespace, so each of their
    # src/ dirs goes on the path. (A pre-split checkout has a top-level toto/
    # instead; keep that working so this file can report the mismatch clearly
    # rather than dying on an ImportError.)
    _portions = sorted((TOTO_SRC / "packages").glob("*/src")) if _is_suite_checkout(TOTO_SRC) else []
    for _portion in _portions or ([TOTO_SRC] if (TOTO_SRC / "toto").is_dir() else []):
        sys.path.insert(0, str(_portion))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _die(msg: str, errors: list[str] | None = None) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"  •  {e}", file=sys.stderr)
        print("\nFix these errors and try again.\n", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _info(msg: str) -> None:
    print(f"  →  {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠  {msg}")


# ---------------------------------------------------------------------------
# Config section parsers
# ---------------------------------------------------------------------------


def _parse_target(cfg: dict) -> dict:
    t = cfg.get("target", {})
    return {
        "type": t.get("type", "local"),
        "host": t.get("host", ""),
        "user": t.get("user", "ubuntu"),
        "key": t.get("key", ""),
        "remote_dir": t.get("remote_dir", "/srv/toto"),
    }


def _parse_ssl(cfg: dict) -> dict:
    s = cfg.get("ssl", {}) or {}
    return {
        "mode": s.get("mode", "gervazy"),
        "email": s.get("email", ""),
        "staging": s.get("staging", False),
        "domain": s.get("domain", ""),
    }


def _parse_tailscale(cfg: dict) -> dict:
    t = cfg.get("tailscale", {})
    return {
        "enabled": t.get("enabled", False),
        "hostname": t.get("hostname", ""),
        "auth_key": t.get("auth_key", ""),
        "authkey_env": t.get("authkey_env", "TS_AUTHKEY"),
    }


# Tailnet is the Tailscale CGNAT range; any resolved address MUST fall inside it so
# we can never accidentally bind a public interface (faros must never be public).
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def _tailscale_ipv4() -> str:
    """Resolve this host's Tailscale IPv4 (via `tailscale ip -4`), asserting it is in
    the tailnet range. Raises a clear error if tailscale is missing/down or the
    address is outside 100.64.0.0/10 (a guard against ever binding a public IP)."""
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        _die("nginx.bind: tailscale — `tailscale` is not installed on this host.")
    if proc.returncode != 0:
        _die(
            "nginx.bind: tailscale — `tailscale ip -4` failed (is tailscaled up? run "
            f"`tailscale up`):\n  {proc.stderr.strip() or proc.stdout.strip()}"
        )
    ip = next((ln.strip() for ln in proc.stdout.splitlines() if ln.strip()), "")
    try:
        if ipaddress.ip_address(ip) not in _TAILNET:
            _die(
                f"nginx.bind: tailscale — resolved IP {ip!r} is not in the tailnet range "
                "(100.64.0.0/10); refusing to bind (it could be a public interface)."
            )
    except ValueError:
        _die(f"nginx.bind: tailscale — could not parse a Tailscale IPv4 from `tailscale ip -4` ({ip!r}).")
    return ip


def _tailscale_url(cfg: dict) -> str:
    """The clearnet-over-tailnet connect URL (`https://<tailnet-ip>:<https_port>`), or
    "" when this config does not bind to tailscale. Single source of truth shared by
    the docker port bind, the FAROS_TAILSCALE_URL env, tailscale.txt and the QR."""
    nginx_cfg = cfg.get("nginx", {}) or {}
    if str(nginx_cfg.get("bind", "")).strip() != "tailscale":
        return ""
    port = nginx_cfg.get("https_port")
    if not port:
        return ""
    return f"https://{_tailscale_ipv4()}:{port}"


def _parse_tor(cfg: dict) -> dict:
    t = cfg.get("tor", {}) or {}
    return {
        "enabled": t.get("enabled", False),
        "image": t.get("image", "osminogin/tor-simple:latest"),
        "onion_port": t.get("onion_port", 80),
        "target": t.get("target", "nginx:80"),
        "service_dir": t.get("service_dir", "onion"),
        # control: expose a password-authed ControlPort on the internal net so the
        # app (toto.nomad) can manage onions over the Tor control protocol.
        "control": t.get("control", False),
        # managed: nomad owns the onion (publishes ephemerally over the control
        # port) → omit the static HiddenServiceDir from the torrc.
        "managed": t.get("managed", False),
    }


def _parse_reachability(cfg: dict) -> dict:
    """Per-transport reachability (nomad). When transport_header is on, nginx stamps
    an unspoofable X-Faros-Transport header — the onion forwards to its own internal
    TLS listener (onion_listener_port), so a clearnet client can't masquerade as one."""
    r = cfg.get("reachability", {}) or {}
    return {
        "transport_header": r.get("transport_header", False),
        "onion_listener_port": r.get("onion_listener_port", 8443),
    }


def _parse_admin(cfg: dict) -> dict:
    a = cfg.get("admin", {}) or {}
    p = a.get("person", {}) or {}
    return {
        "username": a.get("username", "admin"),
        "email": a.get("email", ""),
        "first_name": a.get("first_name", ""),
        "last_name": a.get("last_name", ""),
        "person": {
            "display_name": p.get("display_name", ""),
            "email": p.get("email", ""),
            "phone": p.get("phone", ""),
        },
    }


def _ssl_mode(cfg: dict) -> str:
    """Return ssl.mode (defaults to 'gervazy' when no ssl section is present)."""
    return _parse_ssl(cfg)["mode"]


# ---------------------------------------------------------------------------
# Config loading & validation
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        _die(f"Config file not found: {config_path}")
    try:
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        _die(f"YAML parse error in {config_path}:\n  {exc}")
    if not isinstance(data, dict):
        _die(f"Config file must be a YAML mapping: {config_path}")
    return data


def validate_config(cfg: dict, config_path: Path) -> list[str]:
    errors: list[str] = []
    dep = cfg.get("deployment", {})

    if not dep.get("name"):
        errors.append("deployment.name is required")
    if not dep.get("project_dir"):
        errors.append("deployment.project_dir is required (e.g. 'portal')")
    elif not (ROOT_DIR / dep["project_dir"]).exists():
        errors.append(
            f"deployment.project_dir '{dep['project_dir']}' does not exist"
            f" under {ROOT_DIR}"
        )

    ssl = _parse_ssl(cfg)
    valid_modes = {"gervazy", "tailscale", "letsencrypt", "none"}
    if ssl["mode"] not in valid_modes:
        errors.append(f"ssl.mode must be one of: {', '.join(sorted(valid_modes))}")
    if ssl["mode"] == "letsencrypt":
        if not ssl["email"]:
            errors.append("ssl.email is required when ssl.mode is letsencrypt")
        if not (ssl["domain"] or cfg.get("env", {}).get("PLATFORM_DOMAIN", "")):
            errors.append(
                "ssl.domain (or env.PLATFORM_DOMAIN) is required when ssl.mode is letsencrypt"
            )
    if ssl["mode"] == "tailscale":
        ts = _parse_tailscale(cfg)
        if not ts["hostname"]:
            errors.append("tailscale.hostname is required when ssl.mode is tailscale")

    target = _parse_target(cfg)
    if target["type"] != "local" and not target["host"]:
        errors.append(f"target.host is required when target.type is '{target['type']}'")

    services = cfg.get("services", {})
    env_cfg = cfg.get("env", {})
    if services.get("neo4j") and not env_cfg.get("NEO4J_URI"):
        errors.append("services.neo4j: true  →  NEO4J_URI must be set in the env section")

    # Gitea's ONLY login path is portal SSO (password form disabled), and the
    # authorize endpoint exact-matches redirect URIs that ingress_sso_master
    # registers from PLATFORM_DOMAIN (https unless the domain carries an explicit
    # scheme). Reject the combinations where deploy.py's public base (Gitea's
    # ROOT_URL, see _public_base_url) and the registered redirect URI would
    # drift — a mismatch 400s every login attempt. Grafana shares the derivation
    # but keeps its local admin-password fallback, so it isn't gated here.
    if services.get("gitea"):
        pd = str(env_cfg.get("PLATFORM_DOMAIN", "")).strip().rstrip("/")
        if not pd:
            errors.append(
                "services.gitea: true  →  PLATFORM_DOMAIN must be set in the env "
                "section (the SSO redirect URI is built from it, and SSO is the "
                "only way to log in to Gitea)"
            )
        else:
            if ssl["mode"] == "none" and not pd.startswith("http://"):
                errors.append(
                    "services.gitea with ssl.mode none: PLATFORM_DOMAIN needs an "
                    "explicit http:// scheme — SSO redirect URIs default to https "
                    "and an ssl-none deploy has no TLS listener, so Gitea login "
                    "could never complete"
                )
            bare = pd.split("://", 1)[-1]
            if ssl.get("domain") and ssl["domain"] != bare:
                errors.append(
                    "services.gitea: ssl.domain and PLATFORM_DOMAIN must agree "
                    f"('{ssl['domain']}' vs '{bare}') — the Gitea redirect URI is "
                    "registered from PLATFORM_DOMAIN and exact-matched at "
                    "authorize time"
                )

    tor = _parse_tor(cfg)
    if tor["enabled"] and ssl["mode"] != "none" and str(tor["target"]).endswith(":80"):
        errors.append(
            "tor.enabled with ssl.mode != none must forward to the TLS port: set "
            "tor.target: nginx:443 and tor.onion_port: 443 (forwarding to nginx:80 "
            "would hit the http→https 301 redirect and break the .onion)."
        )

    # faros is never reachable on the public clearnet. By default it publishes on a
    # loopback interface (the only way in from elsewhere is its Tor onion). The opt-in
    # `nginx.bind: tailscale` additionally publishes on the host's tailnet IP, so the
    # clearnet listener is reachable over Tailscale only — still never the public net.
    if dep.get("project_dir") == "faros":
        nginx_cfg = cfg.get("nginx", {}) or {}
        bind = str(nginx_cfg.get("bind", "")).strip()
        if bind == "tailscale":
            if not _parse_tailscale(cfg)["enabled"]:
                errors.append(
                    "nginx.bind: tailscale requires tailscale.enabled: true (the "
                    "clearnet listener is published on the host's tailnet IP)."
                )
            if ssl["mode"] == "none":
                errors.append(
                    "nginx.bind: tailscale needs a TLS clearnet listener: set ssl.mode "
                    "to gervazy/tailscale/letsencrypt (not none)."
                )
            if target["type"] != "local":
                errors.append(
                    "nginx.bind: tailscale is only supported for local targets: the "
                    "tailnet IP is resolved on the box that runs the stack."
                )
        elif bind not in ("127.0.0.1", "::1", "localhost"):
            errors.append(
                "faros must not be reachable on the public network: set "
                "nginx.bind: 127.0.0.1 (same-machine only; reached from outside via "
                "its Tor onion), or nginx.bind: tailscale (loopback + tailnet only). "
                "A non-loopback/empty/0.0.0.0 nginx.bind would expose it publicly and "
                "is not allowed."
            )

    return errors


# ---------------------------------------------------------------------------
# .env management
# ---------------------------------------------------------------------------


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _internal_web_host(name: str) -> str:
    """Underscore-free DNS alias for the web container on the compose network.
    Sibling containers (grafana, gitea) call the portal's OIDC endpoints
    server-side, and Django rejects Host headers containing underscores
    (RFC 1034/1035 validation happens BEFORE ALLOWED_HOSTS is consulted), so the
    web_{name} compose service name is unusable as an HTTP host. build_compose
    attaches this alias to the web service; build_env whitelists it via
    ALLOWED_HOSTS_EXTRA."""
    return f"web-{name}".replace("_", "-")


def _public_base_url(cfg: dict) -> str:
    """Browser-facing base URL for services that register OIDC redirect URIs
    (grafana ROOT_URL/AUTH_URL, gitea ROOT_URL). MUST agree with sso_master's
    get_public_base_url — PLATFORM_DOMAIN first, an explicit scheme respected,
    https otherwise — because the authorize endpoint exact-matches redirect URIs
    and any drift 400s the SSO login. validate_config rejects the combinations
    where the two could still diverge (gitea only: unlike grafana there is no
    admin-password fallback login)."""
    ssl = _parse_ssl(cfg)
    domain = str(
        cfg.get("env", {}).get("PLATFORM_DOMAIN") or ssl.get("domain") or "localhost"
    ).strip().rstrip("/")
    if domain.startswith(("http://", "https://")):
        return domain
    scheme = "http" if (ssl["mode"] or "gervazy") == "none" else "https"
    return f"{scheme}://{domain}"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key, _, val = s.partition("=")
            result[key.strip()] = val.strip()
    return result


def build_env(cfg: dict, existing: dict[str, str]) -> dict[str, str]:
    dep = cfg["deployment"]
    cfg_env = cfg.get("env", {})

    env: dict[str, str] = {}

    for k, v in cfg_env.items():
        env[k] = str(v)

    env["DJANGO_SETTINGS_MODULE"] = f"{dep['project_dir']}.settings"

    # Platform logo, chosen per deployment: faros (the Tor/onion server) → anglerfish;
    # clearnet portal with studio → spider, without studio → wing. (toto core's
    # built-in default when unset is okti_old.png.) The federation logo is always
    # okti.png. An explicit PLATFORM_LOGO_PATH in the config env still wins.
    if "PLATFORM_LOGO_PATH" not in env:
        if dep.get("project_dir") == "faros":
            _logo = "anglerfish.png"
        elif env.get("BUILD_STUDIO", "0") == "1":
            _logo = "spider.png"
        else:
            _logo = "wing.png"
        env["PLATFORM_LOGO_PATH"] = os.path.join("..", "data", "img", _logo)
    env["DB_HOST"] = "postgres"

    # When faros publishes its clearnet listener over the tailnet, hand the resolved
    # connect URL to the app so the welcome page can render a "scan to connect" QR for
    # it (the container has no tailscale itself). Only set when bind == tailscale. Also
    # let Django accept requests arriving on the tailnet IP (Host header + CSRF origin),
    # which isn't known until deploy time.
    def _append_csv(key: str, value: str) -> None:
        parts = [p.strip() for p in env.get(key, "").split(",") if p.strip()]
        if value not in parts:
            parts.append(value)
        env[key] = ",".join(parts)

    _ts_url = _tailscale_url(cfg)
    if _ts_url:
        env["FAROS_TAILSCALE_URL"] = _ts_url
        _ts_ip = _ts_url.split("://", 1)[1].rsplit(":", 1)[0]

        _append_csv("ALLOWED_HOSTS", _ts_ip)
        _append_csv("CSRF_TRUSTED_ORIGINS", _ts_url)

    env["SECRET_KEY"] = existing.get("SECRET_KEY") or secrets.token_urlsafe(50)
    # Precedence: admin.password from the config (authoritative when set) → the
    # existing .env value (stable across redeploys) → a fresh random token.
    # The --admin-password CLI flag still overrides this afterwards (apply_switches).
    env["ADMIN_PASSWORD"] = (
        (cfg.get("admin") or {}).get("password")
        or existing.get("ADMIN_PASSWORD")
        or secrets.token_urlsafe(16)
    )
    env["FIELD_ENCRYPTION_KEY"] = existing.get("FIELD_ENCRYPTION_KEY") or _fernet_key()
    env["SSO_VAULT_PASSWORD"] = existing.get("SSO_VAULT_PASSWORD") or secrets.token_urlsafe(32)

    # Grafana surfaced in the portal (services.grafana). Enabling the service also
    # enables the portal-side UI + OIDC provisioning, so the flags are derived here
    # rather than duplicated in every config. GRAFANA_OIDC_CLIENT_SECRET is the OIDC
    # client secret shared between the web container (which provisions the relying
    # party in ingress_sso_master) and the grafana container (its generic-OAuth
    # config); it is auto-generated and persisted so it stays stable across
    # redeploys. An explicit value in the config env still wins.
    if cfg.get("services", {}).get("grafana"):
        env.setdefault("GRAFANA_ENABLED", "1")
        env.setdefault("GRAFANA_URL", "/grafana/")
        env["GRAFANA_OIDC_CLIENT_SECRET"] = (
            cfg_env.get("GRAFANA_OIDC_CLIENT_SECRET")
            or existing.get("GRAFANA_OIDC_CLIENT_SECRET")
            or secrets.token_urlsafe(32)
        )

    # Gitea surfaced in the portal (services.gitea), same derivation scheme as
    # grafana above. GITEA_OIDC_CLIENT_SECRET is shared between the web container
    # (relying-party provisioning in ingress_sso_master) and the gitea_provision
    # sidecar (gitea admin auth add-oauth). The discovery URL points at the web
    # container's *internal* OIDC discovery variant: gitea's openidConnect provider
    # takes every endpoint from that document and calls token/userinfo itself, so
    # keeping the fetch on the compose network avoids trusting self-signed certs
    # (and works when PLATFORM_DOMAIN is localhost).
    if cfg.get("services", {}).get("gitea"):
        env.setdefault("GITEA_ENABLED", "1")
        env.setdefault("GITEA_URL", "/gitea/")
        env["GITEA_OIDC_CLIENT_SECRET"] = (
            cfg_env.get("GITEA_OIDC_CLIENT_SECRET")
            or existing.get("GITEA_OIDC_CLIENT_SECRET")
            or secrets.token_urlsafe(32)
        )
        env.setdefault(
            "GITEA_OIDC_DISCOVERY_URL",
            f"http://{_internal_web_host(dep['name'])}:8000"
            "/.well-known/openid-configuration-internal",
        )
        # Server-side Gitea REST/clone base (toto.gitvault) and the portal-svc
        # admin account password: the account is created by provision_oauth.sh
        # and used by the web container to mint per-user access tokens.
        # Persisted across redeploys like the OIDC secret above.
        env.setdefault("GITEA_INTERNAL_URL", "http://gitea:3000")
        env["GITEA_SVC_PASSWORD"] = (
            cfg_env.get("GITEA_SVC_PASSWORD")
            or existing.get("GITEA_SVC_PASSWORD")
            or secrets.token_urlsafe(32)
        )

    # Internal SSO callers (grafana token/userinfo, gitea discovery + token/
    # userinfo/jwks) reach the web container directly over the compose network
    # via the underscore-free web-<name> alias (see _internal_web_host). Let
    # Django accept that Host without touching the config's ALLOWED_HOSTS:
    # settings.py appends ALLOWED_HOSTS_EXTRA to whatever ALLOWED_HOSTS resolves
    # to (config-set or default).
    if cfg.get("services", {}).get("grafana") or cfg.get("services", {}).get("gitea"):
        _append_csv("ALLOWED_HOSTS_EXTRA", _internal_web_host(dep["name"]))

    for secret_key in ("DB_PASSWORD", "NEO4J_PASSWORD"):
        if existing.get(secret_key) and secret_key in env:
            env[secret_key] = existing[secret_key]

    # nomad (app-managed Tor onion): the cleartext control-port password is a
    # persisted secret — torrc's HashedControlPassword is derived from this same
    # value at generation time, so the two never drift across redeploys.
    tor = _parse_tor(cfg)
    if tor["control"]:
        env["NOMAD_TOR_CONTROL_HOST"] = "tor"
        env["NOMAD_TOR_CONTROL_PORT"] = "9051"
        env["NOMAD_TOR_CONTROL_PASSWORD"] = (
            existing.get("NOMAD_TOR_CONTROL_PASSWORD") or secrets.token_urlsafe(32)
        )
        env["NOMAD_ONION_PORT"] = str(tor["onion_port"])
        env["NOMAD_ONION_TARGET"] = str(tor["target"])
        env["NOMAD_KEY_DIR"] = "/var/lib/nomad"

    # SSL cert params for the in-container gervazy cert generator
    # (toto.gervazy gen_ssl_cert, run from the web entrypoint when SSL_MODE is
    # gervazy and the cert isn't already present). Mirrors ensure_ssl_for_mode so
    # the cert is identical whether created on the host (laptop/push) or in the
    # container (bare server checkout without host crypto deps).
    ssl = _parse_ssl(cfg)
    env["SSL_MODE"] = ssl["mode"] or "gervazy"
    if env["SSL_MODE"] == "gervazy":
        common_name, dns_names, ip_addresses = _gervazy_cert_params(cfg)
        env["SSL_CERT_COMMON_NAME"] = common_name
        env["SSL_CERT_DNS_NAMES"] = ",".join(dns_names)
        env["SSL_CERT_IP_ADDRESSES"] = ",".join(ip_addresses)

    admin = _parse_admin(cfg)
    env["ADMIN_USERNAME"] = admin["username"]
    for key, val in (
        ("ADMIN_EMAIL", admin["email"]),
        ("ADMIN_FIRST_NAME", admin["first_name"]),
        ("ADMIN_LAST_NAME", admin["last_name"]),
        ("ADMIN_DISPLAY_NAME", admin["person"]["display_name"]),
        ("ADMIN_PERSON_EMAIL", admin["person"]["email"]),
        ("ADMIN_PERSON_PHONE", admin["person"]["phone"]),
    ):
        if val:
            env[key] = val

    return env


def write_env_file(path: Path, env: dict[str, str], header: str = "") -> None:
    lines: list[str] = []
    if header:
        lines.append(header)
    prev_prefix: str | None = None
    for key, val in env.items():
        prefix = key.split("_", 1)[0]
        if prev_prefix is not None and prefix != prev_prefix and lines:
            lines.append("")
        lines.append(f"{key}={val}")
        prev_prefix = prefix
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_env_key(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Nginx config generation
# ---------------------------------------------------------------------------


def _nginx_server_names(cfg: dict) -> str:
    dep = cfg["deployment"]
    name = dep["name"]
    env_cfg = cfg.get("env", {})
    ts = _parse_tailscale(cfg)
    ssl = _parse_ssl(cfg)

    names: list[str] = ["_", "localhost", f"{name}.local"]

    # The Let's Encrypt cert domain must be a server_name so the matching
    # server block (not just the "_" catch-all) terminates TLS for it.
    for domain in (env_cfg.get("PLATFORM_DOMAIN", ""), ssl["domain"]):
        if domain and domain not in names:
            names.append(domain)

    allowed = env_cfg.get("ALLOWED_HOSTS", "")
    for h in allowed.split(","):
        h = h.strip()
        # Skip wildcards, docker service names, bare IPs
        if (
            h
            and "*" not in h
            and not h.startswith("web_")
            and h != "nginx"
            and not h.replace(".", "").isdigit()  # skip bare IPv4
            and h not in names
        ):
            names.append(h)

    if ts["enabled"] and ts["hostname"] and ts["hostname"] not in names:
        names.append(ts["hostname"])

    return " ".join(names)


def _nginx_upload_location(web_svc: str, indent: str = "    ") -> str:
    """Dedicated location for heavy vault file-transfer endpoints — raises body-size
    limit and timeouts. Covers uploads (gateways), encrypt/decrypt, encrypted
    download and the enigma file API: these stream or transform file bytes (S3
    download → crypto → re-upload) and routinely outlive the default 60s
    proxy_read_timeout. A slow *synchronous* request with no bytes flowing is also
    exactly what a Tor circuit drops, so over the .onion the short timeout surfaces
    as a client-side "Network error" on encrypt. Keep this regex ahead of `location /`."""
    return (
        f"{indent}location ~ ^/vault/(gateways/|file/(encrypt|decrypt|download-encrypted)/|api/files/) {{\n"
        f"{indent}    set $backend http://{web_svc}:8000;\n"
        f"{indent}    proxy_pass $backend;\n"
        f"{indent}    proxy_set_header Host $host;\n"
        f"{indent}    proxy_set_header X-Real-IP $remote_addr;\n"
        f"{indent}    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"{indent}    proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"{indent}    client_max_body_size 500m;\n"
        f"{indent}    proxy_connect_timeout 30s;\n"
        f"{indent}    proxy_read_timeout 300s;\n"
        f"{indent}    proxy_send_timeout 300s;\n"
        f"{indent}}}"
    )


def _nginx_proxy_block(web_svc: str, has_ws: bool, indent: str = "    ", transport: str | None = None) -> str:
    ws = ""
    if has_ws:
        ws = (
            f"\n{indent}    proxy_http_version 1.1;"
            f"\n{indent}    proxy_set_header Upgrade $http_upgrade;"
            f'\n{indent}    proxy_set_header Connection "upgrade";'
        )
    # Stamp the transport this listener serves. nginx sets it unconditionally, so a
    # client can't forge it (toto.nomad's reachability gate trusts this header).
    th = f"\n{indent}    proxy_set_header X-Faros-Transport {transport};" if transport else ""
    return (
        f"{indent}location / {{\n"
        f"{indent}    set $backend http://{web_svc}:8000;\n"
        f"{indent}    proxy_pass $backend;\n"
        f"{indent}    proxy_set_header Host $host;\n"
        f"{indent}    proxy_set_header X-Real-IP $remote_addr;\n"
        f"{indent}    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"{indent}    proxy_set_header X-Forwarded-Proto $scheme;{th}\n"
        f"{indent}    proxy_connect_timeout 10s;\n"
        f"{indent}    proxy_read_timeout 60s;{ws}\n"
        f"{indent}}}"
    )


def _nginx_static_locations(indent: str = "    ") -> str:
    return (
        f"{indent}location /static/ {{\n"
        f"{indent}    alias /staticfiles/;\n"
        f"{indent}    expires 30d;\n"
        f"{indent}    access_log off;\n"
        f'{indent}    add_header Cache-Control "public";\n'
        f"{indent}}}\n\n"
        f"{indent}location /media/ {{\n"
        f"{indent}    alias /mediafiles/;\n"
        f"{indent}    expires 30d;\n"
        f"{indent}    access_log off;\n"
        f'{indent}    add_header Cache-Control "public";\n'
        f"{indent}}}\n\n"
        f"{indent}location ~ /\\.ht {{\n"
        f"{indent}    deny all;\n"
        f"{indent}}}"
    )


def _nginx_grafana_location(indent: str = "    ") -> str:
    """Same-origin reverse proxy for Grafana at /grafana/ (emitted only when the
    grafana service runs). Grafana serves from this sub-path (GF_SERVER_ROOT_URL +
    SERVE_FROM_SUB_PATH), so the /grafana/ prefix is preserved to the backend — a
    variable proxy_pass ($backend) forwards the URI unrewritten. WebSocket upgrade
    headers are always set for Grafana Live. Kept ahead of `location /`."""
    return (
        f"{indent}location /grafana/ {{\n"
        f"{indent}    set $backend http://grafana:3000;\n"
        f"{indent}    proxy_pass $backend;\n"
        f"{indent}    proxy_set_header Host $host;\n"
        f"{indent}    proxy_set_header X-Real-IP $remote_addr;\n"
        f"{indent}    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"{indent}    proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"{indent}    proxy_http_version 1.1;\n"
        f"{indent}    proxy_set_header Upgrade $http_upgrade;\n"
        f'{indent}    proxy_set_header Connection "upgrade";\n'
        f"{indent}}}"
    )


def _nginx_gitea_location(indent: str = "    ") -> str:
    """Same-origin reverse proxy for Gitea at /gitea/ (emitted only when the gitea
    service runs). Unlike Grafana, Gitea listens at / and only *generates* links
    with the sub-path (ROOT_URL), so the /gitea prefix must be STRIPPED here —
    the official recipe: `rewrite ^ $request_uri` restores the raw (still-escaped)
    URI incl. query string, the second rewrite drops the prefix, and the variable
    proxy_pass forwards the capture verbatim (a `?` inside an expanded variable is
    not an args separator, so `?service=git-upload-pack` etc. survive intact).
    512M body cap + long timeouts cover large git pushes. Kept ahead of
    `location /`. `^~` short-circuits regex locations: without it the static
    block's `location ~ /\\.ht` deny-all would 403 any repo file named .ht*
    (raw view / API contents of a .htaccess, say) — regexes beat plain prefix
    locations."""
    return (
        f"{indent}location ^~ /gitea/ {{\n"
        f"{indent}    client_max_body_size 512M;\n"
        f"{indent}    set $backend http://gitea:3000;\n"
        f"{indent}    rewrite ^ $request_uri;\n"
        f"{indent}    rewrite ^/gitea(/.*) $1 break;\n"
        f"{indent}    proxy_pass $backend$1;\n"
        f"{indent}    proxy_set_header Host $host;\n"
        f"{indent}    proxy_set_header X-Real-IP $remote_addr;\n"
        f"{indent}    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"{indent}    proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"{indent}    proxy_connect_timeout 10s;\n"
        f"{indent}    proxy_read_timeout 300s;\n"
        f"{indent}    proxy_send_timeout 300s;\n"
        f"{indent}}}"
    )


def _nginx_ssl_params(cert_path: str, key_path: str, indent: str = "    ") -> str:
    return (
        f"{indent}ssl_certificate {cert_path};\n"
        f"{indent}ssl_certificate_key {key_path};\n"
        f"{indent}ssl_session_timeout 1d;\n"
        f"{indent}ssl_session_cache shared:SSL:50m;\n"
        f"{indent}ssl_session_tickets off;\n"
        f"{indent}ssl_protocols TLSv1.2 TLSv1.3;\n"
        f"{indent}ssl_ciphers HIGH:!aNULL:!MD5;\n"
        f"{indent}ssl_prefer_server_ciphers off;\n\n"
        f'{indent}add_header Strict-Transport-Security "max-age=63072000" always;\n'
        f"{indent}add_header X-Content-Type-Options nosniff;\n"
        f'{indent}add_header X-Frame-Options "SAMEORIGIN";\n'
        f'{indent}add_header Referrer-Policy "strict-origin";\n'
        f"{indent}add_header Content-Security-Policy"
        ' "default-src \'self\' http: https: data: blob: \'unsafe-inline\' \'unsafe-eval\';";\n'
    )


def build_nginx_conf(cfg: dict) -> str:
    """Generate nginx.conf from the deployment config."""
    dep = cfg["deployment"]
    name = dep["name"]
    web_svc = f"web_{name}"
    services = cfg.get("services", {})
    ssl = _parse_ssl(cfg)
    ts = _parse_tailscale(cfg)

    ssl_mode = ssl["mode"] or "gervazy"
    server_names = _nginx_server_names(cfg)
    has_ws = services.get("websockets", False)

    resolver = "resolver 127.0.0.11 valid=30s ipv6=off;"
    proxy = _nginx_proxy_block(web_svc, has_ws)
    upload = _nginx_upload_location(web_svc)
    static = _nginx_static_locations()
    # Reverse-proxy Grafana/Gitea same-origin at /grafana/ + /gitea/ when the
    # services run; empty otherwise. Trailing "\n\n" only when present so it
    # doesn't leave blank lines.
    grafana_block = f"{_nginx_grafana_location()}\n\n" if services.get("grafana") else ""
    gitea_block = f"{_nginx_gitea_location()}\n\n" if services.get("gitea") else ""

    if ssl_mode == "none":
        return (
            "# Generated by deploy.py — do not edit manually.\n\n"
            "server {\n"
            "    listen 80;\n"
            "    listen [::]:80;\n"
            f"    server_name {server_names};\n\n"
            f"    {resolver}\n\n"
            "    client_max_body_size 10m;\n"
            "    server_tokens off;\n\n"
            f"{static}\n\n"
            f"{upload}\n\n"
            f"{grafana_block}"
            f"{gitea_block}"
            f"{proxy}\n"
            "}\n"
        )

    if ssl_mode == "tailscale":
        ts_host = ts["hostname"]
        cert = f"/etc/nginx/tailscale-certs/{ts_host}.crt"
        key = f"/etc/nginx/tailscale-certs/{ts_host}.key"
    elif ssl_mode == "letsencrypt":
        domain = ssl["domain"] or cfg.get("env", {}).get("PLATFORM_DOMAIN", "")
        cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        key = f"/etc/letsencrypt/live/{domain}/privkey.pem"
    else:  # gervazy
        cert = "/etc/nginx/cert/localhost.crt"
        key = "/etc/nginx/cert/localhost.key"

    ssl_params = _nginx_ssl_params(cert, key)

    def _tls_block(listen: str, proxy_block: str) -> str:
        return (
            "server {\n"
            f"    listen {listen} ssl;\n"
            "    http2 on;\n"
            f"    server_name {server_names};\n\n"
            f"    {resolver}\n\n"
            "    client_max_body_size 10m;\n\n"
            f"{ssl_params}\n"
            f"{static}\n\n"
            f"{upload}\n\n"
            f"{grafana_block}"
            f"{gitea_block}"
            f"{proxy_block}\n"
            "}\n"
        )

    # Redirect to the *host-mapped* HTTPS port: the container always listens on
    # 443, but when nginx.https_port maps it to e.g. 8443 the browser must be
    # sent to https://host:8443/... — a bare https://$host/... would land on
    # port 443 where nothing is published.
    https_port = int(cfg.get("nginx", {}).get("https_port", 443) or 443)
    _redirect_target = (
        "https://$host$request_uri" if https_port == 443
        else f"https://$host:{https_port}$request_uri"
    )
    redirect_block = (
        "server {\n"
        "    listen 80;\n"
        "    listen [::]:80;\n"
        f"    server_name {server_names};\n"
        f"    return 301 {_redirect_target};\n"
        "}\n"
    )

    reach = _parse_reachability(cfg)
    out = "# Generated by deploy.py — do not edit manually.\n\n" + redirect_block + "\n"
    if reach["transport_header"]:
        # Two TLS listeners: the public/clearnet one (443) and an internal-only one
        # the tor onion forwards to (onion_listener_port, never published to the host).
        # Each stamps its transport so toto.nomad can gate them independently.
        out += _tls_block("443", _nginx_proxy_block(web_svc, has_ws, transport="clearnet")) + "\n"
        out += _tls_block(str(reach["onion_listener_port"]), _nginx_proxy_block(web_svc, has_ws, transport="onion"))
    else:
        out += _tls_block("443", proxy)
    return out


# ---------------------------------------------------------------------------
# Tor onion service (torrc) generation
# ---------------------------------------------------------------------------


def tor_hash_password(password: str) -> str:
    """Compute a torrc ``HashedControlPassword`` — the exact value
    ``tor --hash-password`` prints, but in pure python (no tor binary needed).

    Format: ``16:`` + HEX(salt[8] + indicator[1] + SHA1(salt+password)[20]), using
    the OpenPGP S2K salted-and-iterated hash with Tor's default count indicator.
    """
    indicator = 0x60          # EXPBIAS-encoded iteration count Tor uses by default
    expbias = 6
    count = (16 + (indicator & 15)) << ((indicator >> 4) + expbias)
    salt = os.urandom(8)
    blob = salt + password.encode("utf-8")
    digest = hashlib.sha1()
    remaining = count
    while remaining > 0:
        chunk = blob if remaining >= len(blob) else blob[:remaining]
        digest.update(chunk)
        remaining -= len(chunk)
    return "16:" + (salt.hex() + "60" + digest.digest().hex()).upper()


def build_torrc(cfg: dict, control_password: str | None = None) -> str:
    """Generate a torrc publishing a v3 hidden service that points at nginx.

    When ``tor.managed`` is set the static ``HiddenServiceDir`` is omitted — nomad
    publishes the onion ephemerally over the control port instead. When
    ``tor.control`` is set a password-authed ``ControlPort`` is exposed on the
    internal docker network for nomad to drive.
    """
    tor = _parse_tor(cfg)
    lines = [
        "# Generated by deploy.py — do not edit manually.",
        "SocksPort 0",
        "Log notice stderr",
    ]
    if not tor["managed"]:
        lines += [
            f"HiddenServiceDir /var/lib/tor/{tor['service_dir']}",
            f"HiddenServicePort {tor['onion_port']} {tor['target']}",
            "HiddenServiceVersion 3",
        ]
    if tor["control"]:
        # Bound to the docker network only (never published to the host); guarded
        # by HashedControlPassword whose cleartext rides the web container's env.
        lines.append("ControlPort 0.0.0.0:9051")
        if control_password:
            lines.append(f"HashedControlPassword {tor_hash_password(control_password)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SSL certificate
# ---------------------------------------------------------------------------


def _gervazy_cert_params(cfg: dict) -> tuple[str, list[str], list[str]]:
    """Common name + DNS/IP SANs for the gervazy self-signed cert. Shared by the
    host-side generator (ensure_ssl_for_mode) and the SSL_CERT_* env handed to the
    in-container generator (toto.gervazy gen_ssl_cert), so both paths produce an
    identical cert regardless of where it ends up being created."""
    cert_cfg = cfg.get("cert", {}) or {}
    common_name = cert_cfg.get("common_name", "localhost")
    dns_names = list(cert_cfg.get("dns_names") or ["localhost"])
    ip_addresses = [str(ip) for ip in (cert_cfg.get("ip_addresses") or ["127.0.0.1"])]
    ts = _parse_tailscale(cfg)
    if ts["enabled"] and ts["hostname"] and ts["hostname"] not in dns_names:
        dns_names.append(ts["hostname"])
    return common_name, dns_names, ip_addresses


def ensure_ssl_for_mode(cfg: dict) -> None:
    ssl = _parse_ssl(cfg)
    mode = ssl["mode"] or "gervazy"

    if mode == "gervazy":
        cert_dir = _cert_dir(cfg)
        cert_dir.mkdir(parents=True, exist_ok=True)
        rel = cert_dir.relative_to(ROOT_DIR)
        common_name, dns_names, ip_addresses = _gervazy_cert_params(cfg)
        try:
            from toto.gervazy.crypto import ensure_self_signed_certificate  # noqa: PLC0415
            created = ensure_self_signed_certificate(
                cert_dir / "localhost.crt",
                cert_dir / "localhost.key",
                common_name=common_name,
                dns_names=dns_names,
                ip_addresses=ip_addresses,
            )
            if created:
                _ok(f"Created {rel}/localhost.crt + {rel}/localhost.key (gervazy self-signed)")
            else:
                _ok(f"SSL cert exists ({rel}, gervazy self-signed)")
        except ImportError:
            # No host crypto deps (e.g. a bare server checkout) — the web
            # container generates the cert via `manage.py gen_ssl_cert` on
            # startup, into the shared cert mount, before nginx (which waits for
            # web to be healthy) starts. Nothing to do here.
            _info(
                f"toto.gervazy.crypto not importable on host — gervazy cert will "
                f"be generated in-container into {rel}/ on web startup"
            )

    elif mode == "tailscale":
        ts = _parse_tailscale(cfg)
        _info(
            f"SSL mode: tailscale — run 'tailscale cert {ts['hostname']}' on the host, "
            "or use: python faros/scripts/deploy.py <config> provision"
        )

    elif mode == "letsencrypt":
        domain = ssl["domain"] or cfg.get("env", {}).get("PLATFORM_DOMAIN", "")
        # nginx mounts the host's /etc/letsencrypt read-only and serves
        # live/<domain>/. The deploy doesn't issue the cert — it just reads it from
        # there. Confirm it exists (on the box where nginx runs) and, if not, point
        # at the issuer script. The cert lives on the *host that runs nginx*, so on a
        # push-from-elsewhere flow this check is informational only.
        cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        if os.path.exists(cert_path):
            _ok(f"SSL mode: letsencrypt — found cert for {domain!r} at {cert_path}")
        else:
            _info(
                f"SSL mode: letsencrypt — no cert found at {cert_path}. Issue it ON the "
                f"host that runs nginx with:\n      sudo bash faros/deploy/issue_letsencrypt.sh"
            )

    elif mode == "none":
        _info("SSL mode: none — HTTP only.")


# ---------------------------------------------------------------------------
# docker-compose generation
# ---------------------------------------------------------------------------


def _resolve_build_features(env_cfg: dict) -> dict:
    """Resolve coarse tiers + per-feature flags into effective build decisions.

    Thin adapter over toto.features.resolve_features — the SAME code the portal
    settings run, so deploy.py and settings.py can no longer drift apart. (The
    old inline copy here missed the gitvault→workflows and connectors/formica→
    graph closures; every shipped YAML also sets the owning tier, so effective
    output is unchanged for them.)

    Returns the effective tier booleans (for the image's pip layers + ENV),
    the neo4j-service trigger, the WebSocket/ASGI trigger, and which native
    binaries (tesseract / ffmpeg / texlive) the image needs.
    """
    try:
        from toto.features import resolve_features
    except ImportError:
        _die(
            "the toto suite is not importable — restore the vendored tree "
            "(git checkout -- vendor/toto_libs), set TOTO_SRC to a toto suite "
            "checkout, or install the staged wheels "
            "(pip install --no-index --find-links dist -r requirements.toto.txt)"
        )

    f = resolve_features(env_cfg.get)
    return {
        "studio": f.studio,
        "neo4j": f.neo4j,
        "graph": f.graph,
        "sabbia": f.sabbia,
        "steven": f.steven,
        "needs_channels": f.needs_channels,
        "tesseract": f.tesseract,
        "ffmpeg": f.ffmpeg,
        "texlive": f.texlive,
        "geo": f.geo,
    }


def build_compose(cfg: dict) -> dict:
    dep = cfg["deployment"]
    services = cfg.get("services", {})
    nginx_cfg = cfg.get("nginx", {})
    env_cfg = cfg.get("env", {})
    ssl = _parse_ssl(cfg)
    ssl_mode = ssl["mode"]

    name = dep["name"]
    project_dir = dep["project_dir"]
    web_svc = f"web_{name}"

    # Each project owns its deployment assets under <project_dir>/deploy/ (Dockerfile,
    # entrypoint, monitoring configs). portal and faros differ in DJANGO_SETTINGS_MODULE,
    # WORKDIR, and which requirements layers they install.
    dockerfile = f"{project_dir}/deploy/Dockerfile"

    net = "monitoring" if services.get("prometheus") else f"{name}_net"

    workers = 4
    # Resolve coarse tiers + per-feature flags into effective build decisions
    # (mirrors faros/faros/settings.py). A "1" string is what the Dockerfile
    # ARGs / build conditions expect.
    _feats = _resolve_build_features(env_cfg)
    build_studio = "1" if _feats["studio"] else "0"
    build_neo4j = "1" if _feats["neo4j"] else "0"
    build_steven = "1" if _feats["steven"] else "0"
    build_sabbia = "1" if _feats["sabbia"] else "0"
    # Native binaries, now decoupled: tesseract follows OCR, ffmpeg follows the
    # media file services (fileservices/manta). INSTALL_TESSERACT still pulls both.
    install_tesseract = "1" if _feats["tesseract"] else "0"
    install_ffmpeg = "1" if _feats["ffmpeg"] else "0"
    install_texlive = "1" if _feats["texlive"] else "0"
    # GIS toolchain (GDAL/GEOS/PROJ apt layer) — skipped when BUILD_GEO=0.
    build_geo = "1" if _feats["geo"] else "0"

    # Auto-enable the neo4j service when the graph feature is on and no explicit
    # services.neo4j is set.
    if _feats["graph"] and "neo4j" not in services:
        services = dict(services, neo4j=True)

    # server: explicit field takes precedence; falls back to asgi when any
    # WebSocket feature (chat, latex, pyeditor, sketch, sabbia) is active.
    _default_server = "asgi" if _feats["needs_channels"] else "wsgi"
    server_mode = cfg.get("server", env_cfg.get("SERVER_MODE", _default_server))

    if server_mode == "asgi":
        cmd: list = [
            "uvicorn", f"{project_dir}.asgi:application",
            "--host", "0.0.0.0", "--port", "8000", "--workers", str(workers),
        ]
        server_type = "asgi"
    else:
        cmd = [
            "gunicorn", f"{project_dir}.wsgi:application",
            "--bind", "0.0.0.0:8000", "--workers", str(workers),
        ]
        server_type = "wsgi"

    hc = (
        ["CMD", "curl", "-f", "http://localhost:8000/metrics"]
        if server_type == "wsgi"
        else ["CMD", "curl", "-f", "http://localhost:8000/admin/login/"]
    )

    web_deps: dict[str, Any] = {
        "postgres": {"condition": "service_started"},
        "redis": {"condition": "service_started"},
    }
    if services.get("neo4j"):
        web_deps["neo4j"] = {"condition": "service_started"}
    if services.get("kernel_server"):
        web_deps["kernel_server"] = {"condition": "service_healthy"}

    # Paths relative to .compose/<name>/docker-compose.yaml → ROOT = repo root
    ROOT = "../.."
    # The `:z` suffix on host bind mounts asks Docker to relabel the host path to
    # container_file_t so the container's confined process can read it. Required on
    # SELinux-enforcing hosts (Fedora/RHEL) — without it the host's source tree /
    # generated configs carry a host label the container is denied (manage.py /
    # torrc / nginx.conf read → EACCES). Lowercase `z` (shared) not `Z` because web,
    # celery_worker and kernel_server all mount the same source path. No-op off
    # SELinux (Ubuntu/AppArmor, Docker Desktop), so it is safe everywhere. Do not
    # add it to shared system paths (tailscale/letsencrypt certs, docker.sock).
    web_volumes = [
        f"{ROOT}/{project_dir}:/app/{project_dir}:z",
        f"static_volume:/app/{project_dir}/staticfiles",
        f"{ROOT}/{project_dir}/cert:/app/cert:z",
        f"media_volume:/app/{project_dir}/media",
    ]
    # When nomad owns the onion, the ed25519 key lives in this volume (survives
    # RESET=1 DB wipes and tor/web restarts → stable .onion across redeploys).
    if _parse_tor(cfg)["managed"]:
        web_volumes.append("nomad_data:/var/lib/nomad")

    build_args = {"BUILD_STUDIO": build_studio, "BUILD_NEO4J": build_neo4j,
                  "BUILD_SABBIA": build_sabbia, "BUILD_STEVEN": build_steven,
                  "INSTALL_TESSERACT": install_tesseract, "INSTALL_FFMPEG": install_ffmpeg,
                  "INSTALL_TEXLIVE": install_texlive, "BUILD_GEO": build_geo}

    docker_services: dict[str, Any] = {
        web_svc: {
            "build": {
                "context": ROOT,
                "dockerfile": dockerfile,
                "args": build_args,
            },
            "volumes": web_volumes,
            "command": cmd,
            "expose": ["8000"],
            "env_file": [f"{ROOT}/.env.{name}"],
            "depends_on": web_deps,
            # Self-heal from a transient crash/OOM during first-boot seeding (the
            # init_platform migrate+seed is memory-heavy). Only restarts on a
            # non-zero exit, so a cleanly-stopped container stays down.
            "restart": "on-failure",
            "healthcheck": {
                "test": hc,
                "interval": "10s",
                "timeout": "5s",
                "retries": 12,
                "start_period": "30s",
            },
            # web-<name>: underscore-free alias for server-side OIDC calls from
            # sibling containers (see _internal_web_host).
            "networks": {net: {"aliases": [_internal_web_host(name)]}},
        }
    }

    if services.get("kernel_server"):
        docker_services["kernel_server"] = {
            "build": {"context": ROOT, "dockerfile": dockerfile, "args": build_args},
            "volumes": [
                f"{ROOT}/{project_dir}:/app/{project_dir}:z",
                f"media_volume:/app/{project_dir}/media",
            ],
            "env_file": [f"{ROOT}/.env.{name}"],
            "entrypoint": ["python", "manage.py", "run_kernel_server"],
            "expose": ["5555"],
            "healthcheck": {
                "test": ["CMD-SHELL", "python -c \"import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost',5555)); s.close()\""],
                "interval": "5s",
                "timeout": "5s",
                "retries": 6,
                "start_period": "10s",
            },
            "networks": [net],
        }

    if services.get("celery"):
        # Explicit entrypoint (like kernel_server): the image's ENTRYPOINT is
        # entrypoint.sh, which runs init_platform + ingress_all — only web may do
        # that. Left as a mere `command`, BOTH celery containers re-ran the init
        # after web became healthy; with RESET=1 the two concurrent
        # `init_platform --reset` calls raced each other's DROP SCHEMA (one dies
        # with 'schema "public" does not exist') and re-wiped the DB web had just
        # seeded. depends_on web-healthy guarantees migrations are done.
        docker_services["celery_worker"] = {
            "build": {"context": ROOT, "dockerfile": dockerfile, "args": build_args},
            "entrypoint": ["celery", "-A", project_dir, "worker", "--loglevel=info"],
            # Mount the same media volume as web so file tasks (e.g. vault encrypt)
            # read/write the exact bytes the web container stored.
            "volumes": [
                f"{ROOT}/{project_dir}:/app/{project_dir}:z",
                f"media_volume:/app/{project_dir}/media",
            ],
            "env_file": [f"{ROOT}/.env.{name}"],
            "depends_on": {web_svc: {"condition": "service_healthy"}},
            "networks": [net],
        }
        # A dedicated beat container (not `worker -B`): CELERY_BEAT_SCHEDULE
        # entries (weather refresh, connectors scan) must fire exactly once
        # per stack even if celery_worker is later scaled to N replicas.
        docker_services["celery_beat"] = {
            "build": {"context": ROOT, "dockerfile": dockerfile, "args": build_args},
            "entrypoint": [
                "celery", "-A", project_dir, "beat", "--loglevel=info",
                "-s", "/tmp/celerybeat-schedule",
            ],
            "volumes": [f"{ROOT}/{project_dir}:/app/{project_dir}:z"],
            "env_file": [f"{ROOT}/.env.{name}"],
            "depends_on": {web_svc: {"condition": "service_healthy"}},
            "networks": [net],
        }

    docker_services["postgres"] = {
        "image": "postgis/postgis:15-3.4",
        "environment": {
            "POSTGRES_DB": env_cfg.get("DB_NAME", name),
            "POSTGRES_USER": env_cfg.get("DB_USER", name),
            "POSTGRES_PASSWORD": "${DB_PASSWORD}",
        },
        "volumes": ["postgres_data:/var/lib/postgresql/data"],
        "networks": [net],
    }

    docker_services["redis"] = {
        "image": "redis:7-alpine",
        "volumes": ["redis_data:/data"],
        "networks": [net],
    }

    if services.get("neo4j"):
        _neo4j_cfg = services["neo4j"] if isinstance(services["neo4j"], dict) else {}
        _neo4j_mem = _neo4j_cfg.get("memory", {})
        _heap = _neo4j_mem.get("heap", "1G")
        _pagecache = _neo4j_mem.get("pagecache", "1G")
        docker_services["neo4j"] = {
            "image": "neo4j:5",
            "environment": {
                "NEO4J_AUTH": "${NEO4J_USER}/${NEO4J_PASSWORD}",
                "NEO4J_PLUGINS": '["apoc"]',
                "NEO4J_dbms_security_procedures_unrestricted": "apoc.*",
                "NEO4J_server_memory_heap_initial__size": _heap,
                "NEO4J_server_memory_heap_max__size": _heap,
                "NEO4J_server_memory_pagecache_size": _pagecache,
            },
            "volumes": ["neo4j_data:/data", "neo4j_logs:/logs"],
            "networks": [net],
        }

    http_port = nginx_cfg.get("http_port", 8080)
    https_port = nginx_cfg.get("https_port")
    # nginx.bind restricts the published host interface (e.g. 127.0.0.1) so the box
    # is not reachable on the clearnet — used by the Tor onion deployment. The
    # `tailscale` sentinel publishes on loopback AND the host's tailnet IP, so the
    # clearnet listener is reachable over the tailnet only (never the public net).
    bind = str(nginx_cfg.get("bind", "")).strip()
    if bind == "tailscale":
        bind_hosts = ["127.0.0.1", _tailscale_ipv4()]
    else:
        bind_hosts = [bind]
    ports = []
    for host in bind_hosts:
        pfx = f"{host}:" if host else ""
        ports.append(f"{pfx}{http_port}:80")
        if https_port and ssl_mode != "none":
            ports.append(f"{pfx}{https_port}:443")

    # nginx serves the generated nginx.conf (lives alongside the compose file)
    nginx_volumes: list[str] = [
        "./nginx.conf:/etc/nginx/conf.d/default.conf:ro,z",
        "static_volume:/staticfiles",
        "media_volume:/mediafiles",
    ]
    if ssl_mode == "gervazy":
        nginx_volumes.append(f"{ROOT}/{project_dir}/cert:/etc/nginx/cert:ro,z")
    elif ssl_mode == "tailscale":
        nginx_volumes.append("/var/lib/tailscale/certs:/etc/nginx/tailscale-certs:ro")
    elif ssl_mode == "letsencrypt":
        nginx_volumes.append("/etc/letsencrypt:/etc/letsencrypt:ro")

    docker_services["nginx"] = {
        "image": "nginx:latest",
        "ports": ports,
        "volumes": nginx_volumes,
        "depends_on": {web_svc: {"condition": "service_healthy"}},
        "restart": "unless-stopped",
        "networks": [net],
    }

    tor = _parse_tor(cfg)
    if tor["enabled"]:
        # Publishes a v3 onion service whose HiddenServicePort forwards to nginx
        # over the internal network. The .onion private key lives in tor_data.
        tor_service = {
            "image": tor["image"],
            "command": ["tor", "-f", "/etc/tor/torrc"],
            "volumes": [
                "./torrc:/etc/tor/torrc:ro,z",
                "tor_data:/var/lib/tor",
            ],
            "depends_on": {"nginx": {"condition": "service_started"}},
            "restart": "unless-stopped",
            "networks": [net],
        }
        # Managed onions are published by nomad in the web entrypoint *before* web
        # becomes healthy. nginx waits for web-healthy, so a tor→nginx dependency
        # would deadlock that publish — drop it and let tor's control port come up
        # immediately (it forwards to nginx by DNS once nginx is eventually up).
        if tor["managed"]:
            del tor_service["depends_on"]
            # The osminogin/tor-simple image healthcheck probes the SOCKS proxy, but
            # our torrc sets SocksPort 0 — so it would always report "unhealthy" (a
            # red herring). Nothing depends on tor's health, so disable it.
            tor_service["healthcheck"] = {"disable": True}
        docker_services["tor"] = tor_service

    # Monitoring (Prometheus/Grafana/Loki) is published on loopback only — never on
    # the public interface. Docker bypasses firewalld for published ports, so binding
    # to 127.0.0.1 is what actually keeps these off the internet. Reach them with an
    # SSH tunnel, e.g. `ssh -L 3000:localhost:3000 user@host`. Local dev still uses
    # http://localhost:3000 unchanged.
    if services.get("prometheus"):
        docker_services["prometheus"] = {
            "image": "prom/prometheus",
            "volumes": [f"{ROOT}/{project_dir}/deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:z"],
            # Host 9091, not 9090: 9090 is a popular default (cockpit, jupyter/
            # tornado dev servers…) and a squatted host port fails the whole
            # `up`. In-network scraping (grafana → http://prometheus:9090) is
            # unaffected — this is only the loopback debug publish.
            "ports": ["127.0.0.1:9091:9090"],
            "depends_on": [web_svc],
            "networks": [net],
        }

    if services.get("grafana"):
        grafana_deps = []
        if services.get("prometheus"):
            grafana_deps.append("prometheus")
        if services.get("loki"):
            grafana_deps.append("loki")
        # Grafana is now also reachable through nginx same-origin at
        # {base}/grafana/ (see _nginx_grafana_location) and logs in via this
        # portal's OIDC provider (toto.sso_master). The browser-facing base is the
        # public https host; token/userinfo are called server-side by the grafana
        # container, so they go to the web service INTERNALLY (http://web_*:8000) —
        # that avoids trusting the self-signed gervazy cert. Only the AUTH_URL must
        # be the public URL the browser is redirected to.
        gf_base = _public_base_url(cfg)
        gf_internal = f"http://{_internal_web_host(name)}:8000"
        docker_services["grafana"] = {
            "image": "grafana/grafana",
            # Keep the loopback publish for SSH-tunnel admin/debug access.
            "ports": ["127.0.0.1:3000:3000"],
            "environment": [
                f"GF_SERVER_ROOT_URL={gf_base}/grafana/",
                "GF_SERVER_SERVE_FROM_SUB_PATH=true",
                # Don't ship the admin/admin default — reuse the deployment's
                # persisted admin password (in .env). Admin login is the fallback
                # for local Grafana config; normal access is via portal SSO below.
                "GF_SECURITY_ADMIN_USER=admin",
                "GF_SECURITY_ADMIN_PASSWORD=${ADMIN_PASSWORD}",
                # --- SSO via the portal's OIDC provider (toto.sso_master) ---------
                "GF_AUTH_GENERIC_OAUTH_ENABLED=true",
                "GF_AUTH_GENERIC_OAUTH_NAME=Portal SSO",
                "GF_AUTH_GENERIC_OAUTH_CLIENT_ID=grafana",
                "GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=${GRAFANA_OIDC_CLIENT_SECRET}",
                "GF_AUTH_GENERIC_OAUTH_SCOPES=openid email profile roles",
                f"GF_AUTH_GENERIC_OAUTH_AUTH_URL={gf_base}/sso/authorize/",
                f"GF_AUTH_GENERIC_OAUTH_TOKEN_URL={gf_internal}/sso/token/",
                f"GF_AUTH_GENERIC_OAUTH_API_URL={gf_internal}/sso/userinfo/",
                "GF_AUTH_GENERIC_OAUTH_LOGIN_ATTRIBUTE_PATH=preferred_username",
                "GF_AUTH_GENERIC_OAUTH_EMAIL_ATTRIBUTE_PATH=email",
                "GF_AUTH_GENERIC_OAUTH_NAME_ATTRIBUTE_PATH=name",
                # Superuser-only: map the `roles` claim → Admin, and DENY anyone
                # without a role (strict), so non-superusers can't log in at all.
                "GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH=contains(roles[*], 'admin') && 'Admin'",
                "GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT=true",
                "GF_AUTH_GENERIC_OAUTH_ALLOW_ASSIGN_GRAFANA_ADMIN=true",
                # Skip Grafana's own login screen — go straight to portal SSO.
                "GF_AUTH_OAUTH_AUTO_LOGIN=true",
            ],
            # Mount the two provisioning subdirs individually rather than replacing
            # all of /etc/grafana/provisioning — that keeps the image's default
            # (empty) plugins/ and alerting/ subdirs, which Grafana logs an error
            # for if they go missing.
            "volumes": [
                "grafana_data:/var/lib/grafana",
                f"{ROOT}/{project_dir}/deploy/grafana/provisioning/datasources:/etc/grafana/provisioning/datasources:ro,z",
                f"{ROOT}/{project_dir}/deploy/grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards:ro,z",
                f"{ROOT}/{project_dir}/deploy/grafana/dashboards:/etc/dashboards:ro,z",
            ],
            "depends_on": grafana_deps,
            "networks": [net],
        }

    if services.get("gitea"):
        # Gitea is reachable through nginx same-origin at {base}/gitea/ (see
        # _nginx_gitea_location — the prefix is stripped there; ROOT_URL below is
        # only used by Gitea to *generate* links/redirects). Web login is SSO-only
        # via this portal's OIDC provider: the OAuth source can't be configured by
        # env vars (it's a DB row), so the one-shot gitea_provision sidecar below
        # creates/updates it with the gitea CLI. Discovery/token/userinfo all go
        # container→web over plain internal HTTP (GITEA_OIDC_DISCOVERY_URL points
        # at the internal discovery variant), so no cert trust is needed; only the
        # authorization endpoint advertised by that document is the public URL the
        # browser is redirected to. Staff gating and admin mapping live on the
        # OAuth source (required-claim roles=staff, admin-group admin) — see
        # provision_oauth.sh. Self-contained SQLite in the gitea_data volume.
        # Git-over-SSH is on by default (Gitea's built-in server, published
        # below); git over HTTPS with PATs keeps working through nginx.
        gt_base = _public_base_url(cfg)

        # SSH config: on by default when gitea is enabled, overridable via the
        # bool-or-dict service config (same idiom as `neo4j`) — gitea: {ssh: false}
        # turns it off, gitea: {ssh_port: N} changes the published port.
        _gitea_cfg = services["gitea"] if isinstance(services["gitea"], dict) else {}
        _gitea_ssh = _gitea_cfg.get("ssh", True)
        _gitea_ssh_port = int(_gitea_cfg.get("ssh_port", 2222))
        _ssh_domain = gt_base.split("://", 1)[-1]  # bare host for clone URLs

        # Loopback publish for SSH-tunnel admin/debug (3000 is grafana's).
        gitea_ports = ["127.0.0.1:3300:3000"]
        if _gitea_ssh:
            # Publish the built-in SSH server on the same interface(s) as the git
            # web UI (nginx.bind: empty/tailscale → all interfaces; a specific
            # host → that host only). nginx can't carry SSH (it is HTTP-only, no
            # stream{} block), so this is a directly-published container port.
            _ssh_bind = str(nginx_cfg.get("bind", "")).strip()
            _ssh_pfx = f"{_ssh_bind}:" if _ssh_bind and _ssh_bind != "tailscale" else ""
            gitea_ports.append(f"{_ssh_pfx}{_gitea_ssh_port}:{_gitea_ssh_port}")

        gitea_env = [
            "USER_UID=1000",
            "USER_GID=1000",
            "GITEA__database__DB_TYPE=sqlite3",
            f"GITEA__server__ROOT_URL={gt_base}/gitea/",
            "GITEA__security__INSTALL_LOCK=true",
            # Staff-only instance: no anonymous browsing, no local signup —
            # accounts exist only via OIDC auto-registration. The password
            # sign-in form is hidden so the SSO button is the only way in
            # (basic auth stays on: git clients authenticate with PATs).
            "GITEA__service__REQUIRE_SIGNIN_VIEW=true",
            "GITEA__service__ALLOW_ONLY_EXTERNAL_REGISTRATION=true",
            "GITEA__service__SHOW_REGISTRATION_BUTTON=false",
            "GITEA__service__ENABLE_PASSWORD_SIGNIN_FORM=false",
            "GITEA__oauth2_client__ENABLE_AUTO_REGISTRATION=true",
            # Gitea username := OIDC nickname (the portal username). Do NOT
            # set oauth2_client.OPENID_CONNECT_SCOPES here — it would override
            # the per-source --scopes set by provision_oauth.sh.
            "GITEA__oauth2_client__USERNAME=nickname",
            # auto, NOT login: when auto-registration hits an existing account
            # (same username/email), silently link it to the incoming OIDC
            # identity and sign in. The portal is the sole identity authority
            # here, and its `sub` is a per-row UUID that changes on a RESET=1
            # DB wipe — with `login`, every wipe orphans the persisted Gitea
            # accounts and strands users on an empty "Link to Existing
            # Account" form (there are no local passwords to link with).
            "GITEA__oauth2_client__ACCOUNT_LINKING=auto",
        ]
        if _gitea_ssh:
            # Gitea's own Go SSH server (no in-container OpenSSH); the host key is
            # generated once and persisted in gitea_data. It listens on the
            # published port so the {port}:{port} mapping needs no privileged
            # bind as UID 1000. SSH_DOMAIN/SSH_PORT render the clone URLs.
            gitea_env += [
                "GITEA__server__DISABLE_SSH=false",
                "GITEA__server__START_SSH_SERVER=true",
                f"GITEA__server__SSH_DOMAIN={_ssh_domain}",
                f"GITEA__server__SSH_PORT={_gitea_ssh_port}",
                f"GITEA__server__SSH_LISTEN_PORT={_gitea_ssh_port}",
            ]
        else:
            gitea_env.append("GITEA__server__DISABLE_SSH=true")

        docker_services["gitea"] = {
            "image": "gitea/gitea:1.24",
            # Boot after web: gitea re-registers persisted OAuth sources at
            # startup and fetches the discovery URL then — before web is up the
            # web-portal alias doesn't even resolve, logging a scary (though
            # self-healing) "[E] Unable to register source" on every boot.
            "depends_on": {web_svc: {"condition": "service_healthy"}},
            "ports": gitea_ports,
            "environment": gitea_env,
            "volumes": ["gitea_data:/data"],
            "healthcheck": {
                "test": ["CMD", "curl", "-f", "http://localhost:3000/api/healthz"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 12,
                "start_period": "30s",
            },
            "restart": "unless-stopped",
            "networks": [net],
        }
        # One-shot provisioner for the OIDC auth source (idempotent add-or-update;
        # see provision_oauth.sh). Needs gitea healthy (app.ini written, DB
        # migrated) and web healthy (add-oauth itself fetches the discovery URL).
        docker_services["gitea_provision"] = {
            "image": "gitea/gitea:1.24",
            "entrypoint": ["/bin/sh", "/provisioning/provision_oauth.sh"],
            "environment": [
                "GITEA_OIDC_CLIENT_SECRET=${GITEA_OIDC_CLIENT_SECRET}",
                "GITEA_OIDC_DISCOVERY_URL=${GITEA_OIDC_DISCOVERY_URL}",
                "GITEA_OIDC_SOURCE_NAME=${GITEA_OIDC_SOURCE_NAME:-portal-sso}",
                # portal-svc admin account (gitvault token minting), see script.
                "GITEA_SVC_PASSWORD=${GITEA_SVC_PASSWORD}",
            ],
            "volumes": [
                "gitea_data:/data",
                f"{ROOT}/{project_dir}/deploy/gitea/provision_oauth.sh:/provisioning/provision_oauth.sh:ro,z",
            ],
            "depends_on": {
                "gitea": {"condition": "service_healthy"},
                web_svc: {"condition": "service_healthy"},
            },
            "restart": "no",
            "networks": [net],
        }

    if services.get("loki"):
        docker_services["loki"] = {
            "image": "grafana/loki:2.9.0",
            "user": "0",
            "volumes": [
                f"{ROOT}/{project_dir}/deploy/loki/config.yaml:/etc/loki/local-config.yaml:ro,z",
                "loki_data:/loki",
                "loki_wal:/wal",
            ],
            "entrypoint": ["/bin/sh", "-c"],
            "command": [
                "mkdir -p /loki/index /loki/cache /loki/chunks /loki/compactor && "
                "exec /usr/bin/loki -config.file=/etc/loki/local-config.yaml"
            ],
            "ports": ["127.0.0.1:3100:3100"],
            "restart": "unless-stopped",
            "networks": [net],
        }
        docker_services["promtail"] = {
            "image": "grafana/promtail:2.9.0",
            "volumes": [
                "/var/lib/docker/containers:/var/lib/docker/containers:ro",
                "/var/run/docker.sock:/var/run/docker.sock",
                f"{ROOT}/{project_dir}/deploy/promtail/promtail-config.yaml:/etc/promtail/config.yml:z",
            ],
            "command": "-config.file=/etc/promtail/config.yml",
            "restart": "unless-stopped",
            "networks": [net],
        }

    volumes: dict[str, Any] = {
        "static_volume": None,
        "postgres_data": None,
        "media_volume": None,
        "redis_data": None,
    }
    if services.get("neo4j"):
        volumes.update({"neo4j_data": None, "neo4j_logs": None})
    if tor["enabled"]:
        volumes["tor_data"] = None
    if tor["managed"]:
        volumes["nomad_data"] = None
    if services.get("grafana"):
        volumes["grafana_data"] = None
    if services.get("gitea"):
        volumes["gitea_data"] = None
    if services.get("loki"):
        volumes.update({"loki_data": None, "loki_wal": None})

    return {
        "services": docker_services,
        "volumes": volumes,
        "networks": {net: None},
    }


# ---------------------------------------------------------------------------
# Remote execution helpers
# ---------------------------------------------------------------------------


def _ssh_base(target: dict) -> list[str]:
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    if target["key"]:
        cmd += ["-i", os.path.expanduser(target["key"])]
    cmd.append(f"{target['user']}@{target['host']}")
    return cmd


def ssh_run(target: dict, remote_cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(_ssh_base(target) + [remote_cmd], check=check)


DIST_DIR = ROOT_DIR / "dist"


def _toto_versioning():
    """The version contract, imported from the toto checkout (toto-base ships it)."""
    try:
        import toto.versioning as versioning
        return versioning
    except ImportError:
        _die(
            "toto.versioning is not importable — this host cannot verify which "
            "toto version it is building.\n"
            "  Restore the vendored tree (git checkout -- vendor/toto_libs), "
            f"point TOTO_SRC (or `toto_src:` in the yaml) at a toto suite checkout "
            f"of v{_manifest_version_hint()}, or install the suite "
            "(pip install --no-index --find-links dist -r requirements.toto.txt).\n"
            "  A checkout older than v1.0 predates the multi-package split."
        )


def _manifest_version_hint() -> str:
    """Best-effort version from the manifest, for error messages only."""
    try:
        for line in TOTO_MANIFEST.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("toto-") and "==" in line:
                return line.split("==", 1)[1].strip()
    except OSError:
        pass
    return "<see requirements.toto.txt>"


def read_toto_manifest():
    """The packages and exact version this host requires."""
    versioning = _toto_versioning()
    try:
        return versioning.read_manifest(TOTO_MANIFEST)
    except versioning.TotoVersionError as exc:
        _die(str(exc))


def stage_toto_wheels(cfg: dict, dev: bool = False) -> list[Path]:
    """Build exactly the toto wheels this host pins, and prove they are those.

    The Dockerfiles install from dist/ against requirements.toto.txt, so what is
    staged here is exactly what runs. Source precedence:
      1. a toto suite checkout — `toto_src:` in the yaml, the TOTO_SRC env var,
         or the in-repo vendored tree at vendor/toto_libs (the default).
      2. `toto_requirement:` in the yaml — a list of complete pip requirement
         strings, one per pinned package, supplied by you (no git host is
         assumed or synthesised).

    The build is rejected unless the checkout is at the pinned version, and again
    unless the built wheels carry it. `dev=True` relaxes only the checkout's
    tag/clean-tree requirement, and is refused for anything that leaves this
    machine.
    """
    versioning = _toto_versioning()
    manifest = read_toto_manifest()

    src = None
    raw = cfg.get("toto_src")
    if raw and isinstance(raw, str):
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT_DIR / p).resolve()
        if not _is_suite_checkout(p):
            _die(f"toto_src '{p}' is not a toto suite checkout (no VERSION + packages/)")
        src = p
    elif TOTO_SRC is not None:
        src = TOTO_SRC

    DIST_DIR.mkdir(exist_ok=True)
    # Wheel filenames normalise '-' to '_' (toto-base -> toto_base-1.0-...whl),
    # so match both spellings when clearing stale builds.
    for stale in list(DIST_DIR.glob("toto-*.whl")) + list(DIST_DIR.glob("toto_*.whl")):
        stale.unlink()

    built_version = manifest.version
    if src is not None:
        try:
            built_version = versioning.verify_checkout(src, manifest, strict=not dev)
        except versioning.TotoVersionError as exc:
            _die(str(exc))
        targets = [(name, str(versioning.package_dir(src, name))) for name in manifest.names]
        origin = f"checkout {src} (v{built_version})"
    else:
        reqs = cfg.get("toto_requirement")
        if isinstance(reqs, str):
            _die(
                "`toto_requirement:` must now be a LIST of complete requirement "
                "strings, one per pinned package, e.g.\n"
                "  toto_requirement:\n"
                f"    - 'toto-base @ git+https://<your-git-host>/<you>/toto_libs.git"
                f"@v{manifest.version}#subdirectory=packages/toto-base'"
            )
        if not reqs or not isinstance(reqs, list):
            _die(
                "cannot stage the toto wheels: no toto suite checkout found "
                "(`toto_src:` in the yaml, TOTO_SRC env, or the in-repo "
                "vendor/toto_libs tree) and no `toto_requirement:` list in the "
                "config.\n"
                "  Simplest fix: restore the vendored tree with "
                "`git checkout -- vendor/toto_libs`."
            )
        targets = [(None, str(req)) for req in reqs]
        origin = "toto_requirement pins"

    for name, target in targets:
        # setuptools reuses <package>/build/lib and copies whatever is in it into
        # the wheel, so a file deleted from the checkout keeps shipping until that
        # tree is cleared. build/ is gitignored, so this only bites on a machine
        # that built before the deletion — exactly the case that must not produce
        # a silently wrong image.
        stale_build = Path(target) / "build"
        if stale_build.is_dir():
            shutil.rmtree(stale_build, ignore_errors=True)

        res = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--wheel-dir", str(DIST_DIR), target],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            _die(
                f"building the {name or 'toto'} wheel from {target} failed:\n"
                f"{res.stdout[-1500:]}\n{res.stderr[-1500:]}"
            )

    wheels = sorted(list(DIST_DIR.glob("toto-*.whl")) + list(DIST_DIR.glob("toto_*.whl")))
    try:
        versioning.verify_wheels(wheels, manifest, exact_version=not dev)
    except versioning.TotoVersionError as exc:
        _die(str(exc))

    if dev:
        _warn(
            f"DEV BUILD — staged toto {built_version} from {src} as it is right now; "
            f"this host pins {manifest.version}. Local use only: --dev is refused for push."
        )
    _ok(f"Staged {len(wheels)} toto wheels ({built_version}) from {origin}")
    return wheels


def rsync_to_remote(target: dict) -> None:
    ssh_args = "-o StrictHostKeyChecking=no -o BatchMode=yes"
    if target["key"]:
        ssh_args += f" -i {os.path.expanduser(target['key'])}"

    remote = f"{target['user']}@{target['host']}:{target['remote_dir']}/"
    excludes = [
        "--exclude=.git/",
        "--exclude=venv/",
        # The clean-env gate builds .venv_test/ at the repo root — ~0.5-1 GB
        # that must never be rsynced to a server.
        "--exclude=.venv_test*/",
        "--exclude=.venv/",
        "--exclude=__pycache__/",
        "--exclude=*.pyc",
        "--exclude=*.egg-info/",
        "--exclude=node_modules/",
        "--exclude=enigma/enigma/src-tauri/target/",
        "--exclude=*.sqlite3",
        "--exclude=*.log",
        "--exclude=staticfiles/",
        "--exclude=media/",
        "--exclude=out/",
        # vendor/toto_libs ships (the wheels build from it on the target), but
        # not its local junk. Anchored: the repo-root dist/ must keep shipping.
        "--exclude=/vendor/toto_libs/dist/",
        "--exclude=/vendor/toto_libs/packages/*/build/",
        "--exclude=/vendor/toto_libs/.venv*/",
    ]
    cmd = [
        "rsync", "-avz", "--delete",
        "-e", f"ssh {ssh_args}",
        *excludes,
        str(ROOT_DIR) + "/",
        remote,
    ]
    _info(f"Rsyncing to {target['host']}:{target['remote_dir']} ...")
    subprocess.run(cmd, check=True)
    _ok("Rsync complete")


def remote_compose_run(
    target: dict,
    name: str,
    *args: str,
    project_name: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    rd = target["remote_dir"]
    compose_file = f"{rd}/.compose/{name}/docker-compose.yaml"
    env_file = f"{rd}/.env.{name}"
    cmd_parts = [
        f"cd {rd}",
        f"&& docker compose --env-file {env_file} -f {compose_file}",
    ]
    if project_name:
        cmd_parts.append(f"-p {project_name}")
    cmd_parts.append(" ".join(str(a) for a in args))
    return ssh_run(target, " ".join(cmd_parts), check=check)


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

_DOCKER_INSTALL_SCRIPT = """\
set -e
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
if ! groups $USER | grep -q '\\bdocker\\b'; then
    sudo usermod -aG docker "$USER"
fi
docker --version
"""

_TAILSCALE_INSTALL_SCRIPT = """\
set -e
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
sudo systemctl enable --now tailscaled 2>/dev/null || true
tailscale version
"""


def check_tailscale_config(cfg: dict, target: dict) -> None:
    """Check Tailscale configuration and warn about issues before deployment."""
    ts = _parse_tailscale(cfg)
    ssl = _parse_ssl(cfg)

    if not ts["enabled"] and ssl["mode"] != "tailscale":
        return

    _info("Checking Tailscale configuration ...")

    # Auth key check (relevant for non-local targets needing provisioning)
    auth_key = ts["auth_key"] or os.environ.get(ts["authkey_env"], "")
    if not auth_key and target["type"] != "local":
        _warn(
            f"No Tailscale auth key found (tailscale.auth_key or env {ts['authkey_env']!r}). "
            "Remote provisioning will require manual 'tailscale up' on the host."
        )
    elif auth_key:
        _ok("Tailscale auth key found")

    # For local targets: check daemon and cert files
    if target["type"] == "local":
        result = subprocess.run(
            ["tailscale", "status"], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if "connect" in stderr_lower or "not running" in stderr_lower or result.returncode == 127:
                _warn(
                    "Tailscale daemon is not running or CLI not found. "
                    "Start it with: sudo systemctl start tailscaled"
                )
            else:
                _warn(f"tailscale status returned non-zero: {result.stderr.strip()}")
        else:
            _ok("Tailscale daemon is running")

        # Check cert files when ssl.mode=tailscale
        if ssl["mode"] == "tailscale" and ts["hostname"]:
            crt = Path(f"/var/lib/tailscale/certs/{ts['hostname']}.crt")
            key = Path(f"/var/lib/tailscale/certs/{ts['hostname']}.key")
            if crt.exists() and key.exists():
                _ok(f"Tailscale certs found for {ts['hostname']}")
            else:
                _warn(
                    f"Tailscale certs not found at /var/lib/tailscale/certs/{ts['hostname']}.*  "
                    f"Run: sudo tailscale cert {ts['hostname']}"
                )


# Install certbot via whatever package manager the host has (the OVH image is
# Fedora → dnf; Debian/Ubuntu → apt-get). Shared by provision + renew.
_CERTBOT_INSTALL_SH = """\
if ! command -v certbot >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then
        sudo dnf -y install certbot
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y certbot
    else
        echo "No supported package manager (dnf/apt-get) to install certbot" >&2
        exit 1
    fi
fi
"""

# Global certbot renewal hooks: pause the nginx container around an actual renewal
# so the --standalone authenticator can bind :80. certbot runs these ONLY when a
# cert is genuinely renewed, so no-op renew runs cause no downtime. Project-agnostic
# (matches any running container whose name contains 'nginx'). Installed by both
# provision and renew so the host's certbot-renew timer auto-renews too.
_CERTBOT_RENEWAL_HOOKS_SH = """\
sudo mkdir -p /etc/letsencrypt/renewal-hooks/pre /etc/letsencrypt/renewal-hooks/post
sudo tee /etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh >/dev/null <<'HOOK'
#!/bin/sh
docker ps -q -f name=nginx | xargs -r docker stop
HOOK
sudo tee /etc/letsencrypt/renewal-hooks/post/start-nginx.sh >/dev/null <<'HOOK'
#!/bin/sh
docker ps -aq -f name=nginx | xargs -r docker start
HOOK
sudo chmod +x /etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh \\
              /etc/letsencrypt/renewal-hooks/post/start-nginx.sh
"""


def cmd_renew(cfg: dict, *, force: bool = False) -> None:
    """Renew the Let's Encrypt cert via certbot (host certbot, ssh for remote).

    certbot only renews a cert within ~30 days of expiry (use force=True to renew
    regardless). The renewal hooks pause/restart the nginx container around an
    actual renewal so the standalone authenticator can bind :80; they no-op when
    nothing is due, so this is safe to run (or cron) anytime with no downtime.
    The renewed cert stays in the host's /etc/letsencrypt — same path, not recreated.
    """
    target = _parse_target(cfg)
    ssl = _parse_ssl(cfg)
    if ssl["mode"] != "letsencrypt":
        _die(f"renew applies only to ssl.mode: letsencrypt (this config uses {ssl['mode']!r}).")
    domain = ssl["domain"] or cfg.get("env", {}).get("PLATFORM_DOMAIN", "")
    where = "localhost" if target["type"] == "local" else target["host"]
    _info(f"Renewing Let's Encrypt cert for {domain} on {where} ...")
    script = (
        "set -e\n"
        + _CERTBOT_INSTALL_SH
        + _CERTBOT_RENEWAL_HOOKS_SH
        + f"sudo certbot renew{' --force-renewal' if force else ''}\n"
    )
    if target["type"] == "local":
        subprocess.run(["bash", "-c", script], check=False)
    else:
        ssh_run(target, script, check=False)
    _ok(
        "certbot renew complete — no-op unless the cert is within ~30 days of expiry"
        f"{' (forced)' if force else ' (pass --force to renew regardless)'}. "
        "nginx is paused only during an actual renewal; the cert path is unchanged."
    )


def cmd_provision(cfg: dict) -> None:
    target = _parse_target(cfg)
    ts = _parse_tailscale(cfg)
    ssl = _parse_ssl(cfg)

    if target["type"] == "local":
        _die("provision is only for non-local targets — set target.type: ec2 | ovh | device")

    _info(f"Provisioning {target['type']} host: {target['host']}")
    ssh_run(target, f"mkdir -p {target['remote_dir']}")
    _ok(f"Remote dir ready: {target['remote_dir']}")

    _info("Installing Docker ...")
    ssh_run(target, _DOCKER_INSTALL_SCRIPT)
    _ok("Docker ready")

    if ts["enabled"]:
        _info("Installing Tailscale ...")
        ssh_run(target, _TAILSCALE_INSTALL_SCRIPT)

        auth_key = ts["auth_key"] or os.environ.get(ts["authkey_env"], "")
        if not auth_key:
            _warn(
                f"No Tailscale auth key found (checked tailscale.auth_key and "
                f"env var {ts['authkey_env']!r}). "
                "Run 'sudo tailscale up' manually on the host after provisioning."
            )
        else:
            hostname_flag = f"--hostname={ts['hostname']}" if ts["hostname"] else ""
            ssh_run(
                target,
                f"sudo tailscale up --authkey={auth_key} {hostname_flag} --accept-routes || true",
                check=False,
            )
            _ok(f"Tailscale joined{(' as ' + ts['hostname']) if ts['hostname'] else ''}")

        if ssl["mode"] == "tailscale" and ts["hostname"]:
            _info(f"Requesting Tailscale HTTPS cert for {ts['hostname']} ...")
            ssh_run(
                target,
                (
                    f"sudo tailscale cert {ts['hostname']} && "
                    f"sudo chmod 644 /var/lib/tailscale/certs/{ts['hostname']}.crt "
                    f"/var/lib/tailscale/certs/{ts['hostname']}.key"
                ),
                check=False,
            )
            _ok("Tailscale cert provisioned")

    if ssl["mode"] == "letsencrypt":
        domain = ssl["domain"] or cfg.get("env", {}).get("PLATFORM_DOMAIN", "")
        email = ssl["email"]
        staging_flag = "--staging" if ssl["staging"] else ""
        _info(f"Requesting Let's Encrypt cert for {domain} ...")
        # certbot --standalone binds host port 80 for the ACME http-01 challenge, so
        # this must run while nothing else holds :80 — provision always runs BEFORE
        # push (nginx not up yet), so that holds. The cert lands in the host's
        # /etc/letsencrypt, which nginx mounts read-only and which survives push,
        # image rebuilds and RESET=1. --keep-until-expiring + --cert-name make a
        # re-run a no-op when a valid cert already exists, so it is issued ONCE and
        # never recreated (avoids Let's Encrypt's duplicate-cert rate limit).
        ssh_run(target, f"""
set -e
{_CERTBOT_INSTALL_SH}
# firewalld is a host process, so it gates the standalone challenge on :80 (Docker
# -published ports bypass it, but the certbot host process does not). Opening
# http/https is idempotent and harmless.
if command -v firewall-cmd >/dev/null 2>&1; then
    sudo firewall-cmd --add-service=http --add-service=https --permanent || true
    sudo firewall-cmd --reload || true
fi
sudo certbot certonly --standalone --non-interactive --agree-tos --keep-until-expiring \\
    --cert-name {domain} --email {email} -d {domain} {staging_flag}
# Install renewal hooks so future `certbot renew` runs (this script's host timer,
# or `deploy.py <config> renew`) pause nginx around an actual renewal.
{_CERTBOT_RENEWAL_HOOKS_SH}
""", check=False)
        _ok(f"Let's Encrypt cert for {domain} lives in /etc/letsencrypt on the host "
            "(persists across redeploys; re-running provision keeps the existing cert). "
            "Renew later with:  python faros/scripts/deploy.py <config> renew")

    _ok(f"Host {target['host']} is ready.")
    _info("Next step:  python faros/scripts/deploy.py <config> push")


def cmd_push(
    cfg: dict,
    compose_file: Path,
    env_file: Path,
    *,
    detach: bool,
    project_name: str | None,
) -> None:
    target = _parse_target(cfg)
    dep = cfg["deployment"]
    name = dep["name"]

    if target["type"] == "local":
        _die("push is only for non-local targets — set target.type: ec2 | ovh | device")

    rsync_to_remote(target)

    _info("Starting stack on remote host ...")
    up_args = ["up", "--build", "-d"]
    remote_compose_run(target, name, *up_args, project_name=project_name)
    _ok("Stack started on remote host")


# ---------------------------------------------------------------------------
# Docker Compose helpers (local)
# ---------------------------------------------------------------------------


def compose_run(
    compose_file: Path,
    env_file: Path,
    *args: str,
    project_name: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    cmd = [
        "docker", "compose",
        "--env-file", str(env_file),
        "-f", str(compose_file),
    ]
    if project_name:
        cmd += ["-p", project_name]
    return subprocess.run([*cmd, *args], cwd=ROOT_DIR, check=check)


def is_running(compose_file: Path, env_file: Path, project_name: str) -> bool:
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(env_file),
            "-f", str(compose_file),
            "-p", project_name,
            "ps", "-q",
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _identify_and_show_failed_services(
    compose_file: Path,
    env_file: Path,
    project_name: str | None,
    tail: int = 60,
) -> None:
    result = subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(env_file),
            "-f", str(compose_file),
            *((["-p", project_name]) if project_name else []),
            "ps", "--all", "--format", "{{.Service}}\t{{.ExitCode}}",
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    failed = []
    for line in result.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2 and parts[1] not in ("0", ""):
            failed.append(parts[0])

    if not failed:
        compose_run(compose_file, env_file, "logs", f"--tail={tail}",
                    project_name=project_name, check=False)
        return

    for svc in failed:
        print(f"\n  Service '{svc}' exited. Last {tail} log lines:\n")
        compose_run(compose_file, env_file, "logs", f"--tail={tail}", svc,
                    project_name=project_name, check=False)


def build_fingerprint(cfg: dict) -> str:
    """Hash of everything baked into the docker image (bind-mounted project
    code is live and does NOT require a rebuild): Dockerfile, entrypoint,
    requirements files, download_vendor.py, data/, and the toto library
    source (git state of the checkout, else the staged wheel)."""
    h = hashlib.sha256()
    proj = cfg["deployment"]["project_dir"]
    files = [
        ROOT_DIR / proj / "deploy" / "Dockerfile",
        ROOT_DIR / proj / "deploy" / "entrypoint.sh",
        ROOT_DIR / proj / "scripts" / "download_vendor.py",
    ]
    files += sorted(ROOT_DIR.glob("requirements*.txt"))
    files += sorted(p for p in (ROOT_DIR / "data").rglob("*") if p.is_file())
    for p in files:
        if p.is_file():
            h.update(str(p.relative_to(ROOT_DIR)).encode())
            h.update(p.read_bytes())
    if TOTO_SRC and (Path(TOTO_SRC) / ".git").exists():
        for cmd in (["rev-parse", "HEAD"], ["status", "--porcelain"], ["diff"]):
            out = subprocess.run(["git", "-C", str(TOTO_SRC), *cmd],
                                 capture_output=True, text=True, check=False)
            h.update(out.stdout.encode())
    elif TOTO_SRC and TOTO_SRC.is_relative_to(ROOT_DIR) and (ROOT_DIR / ".git").exists():
        # The vendored subtree has no .git of its own; its state is the host
        # repo's git state of the prefix. Hashed here because --smart's verdict
        # is taken BEFORE stage_toto_wheels runs, so hashing the staged wheels
        # would freeze the previous build's toto forever.
        rel = str(TOTO_SRC.relative_to(ROOT_DIR))
        for cmd in (["log", "-1", "--format=%H", "--", rel],
                    ["status", "--porcelain", "--", rel],
                    ["diff", "--", rel]):
            out = subprocess.run(["git", "-C", str(ROOT_DIR), *cmd],
                                 capture_output=True, text=True, check=False)
            h.update(out.stdout.encode())
    else:
        for whl in sorted(list((ROOT_DIR / "dist").glob("toto-*.whl"))
                          + list((ROOT_DIR / "dist").glob("toto_*.whl"))):
            h.update(whl.name.encode())
            h.update(whl.read_bytes())
    return h.hexdigest()


def run_up(
    compose_file: Path,
    env_file: Path,
    *,
    detach: bool,
    project_name: str | None,
    build: bool = True,
) -> None:
    # Without --build, compose reuses existing images and only builds ones
    # that don't exist yet (image reuse for fast repeated local deploys).
    up_args = ["up", "--build"] if build else ["up"]
    if detach:
        up_args.append("-d")
    try:
        compose_run(compose_file, env_file, *up_args, project_name=project_name)
    except subprocess.CalledProcessError:
        _identify_and_show_failed_services(compose_file, env_file, project_name)
        _die(
            "Stack failed to start.\n"
            "  Check the logs above for the root cause.\n"
            f"  Or run:  docker compose -f {compose_file} logs"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy a toto stack from a YAML config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml up --no-reset-db -d\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml logs\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml validate\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml nginx\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml provision\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml push\n"
            "  python faros/scripts/deploy.py faros/deploy/configs/faros_onion_tls.yaml renew\n"
        ),
    )
    parser.add_argument("config", type=Path, help="Path to deployment YAML config file")
    parser.add_argument(
        "command",
        nargs="?",
        default="up",
        choices=(
            "up", "down", "stop", "restart", "logs",
            "config", "env", "nginx", "tor", "onion", "tailscale", "validate",
            "provision", "push", "renew",
        ),
        help="Command to run (default: up)",
    )
    parser.add_argument("-d", "--detach", action="store_true", help="Run detached")
    parser.add_argument(
        "--no-build", dest="no_build", action="store_true",
        help="Reuse existing docker images on up/restart: skip the image "
             "rebuild and the wasm/wheel staging (compose still builds any "
             "image that does not exist yet)")
    parser.add_argument(
        "--smart", action="store_true",
        help="Rebuild the image on up/restart only when its build inputs "
             "changed since the last built deploy (Dockerfile, entrypoint, "
             "requirements, download_vendor.py, data/, toto source); "
             "otherwise behave like --no-build")
    parser.add_argument(
        "--dev", action="store_true",
        help="Build against the toto tree as it is right now, instead of "
             "requiring it to match the version pinned in requirements.toto.txt. "
             "For local up/restart only — refused for push")
    parser.add_argument(
        "--force", action="store_true",
        help="renew: force certificate renewal even if not near expiry",
    )
    parser.add_argument(
        "--service",
        action="store_true",
        help="Run as a named Compose project (stable name, safe to restart in-place)",
    )
    dg = parser.add_mutually_exclusive_group()
    dg.add_argument("--debug", action="store_true", help="Set DEBUG=1 in .env")
    dg.add_argument("--no-debug", action="store_true", help="Set DEBUG=0 in .env")
    rg = parser.add_mutually_exclusive_group()
    rg.add_argument("--reset-db", action="store_true", help="Set RESET=1 (wipe + seed DB)")
    rg.add_argument("--no-reset-db", action="store_true", help="Set RESET=0 (keep DB)")
    fg = parser.add_mutually_exclusive_group()
    fg.add_argument("--fake-data", dest="full_ingress", action="store_true",
                    help="Set FULL_INGRESS=1")
    fg.add_argument("--no-fake-data", dest="no_full_ingress", action="store_true",
                    help="Set FULL_INGRESS=0")
    parser.add_argument("--admin-password", metavar="PASSWORD", help="Override ADMIN_PASSWORD")
    return parser


def apply_switches(args: argparse.Namespace, env_file: Path) -> None:
    if args.debug:
        update_env_key(env_file, "DEBUG", "1")
        _info("DEBUG=1")
    elif args.no_debug:
        update_env_key(env_file, "DEBUG", "0")
        _info("DEBUG=0")

    if args.reset_db:
        update_env_key(env_file, "RESET", "1")
        _info("RESET=1  (DB will be wiped on startup)")
    elif args.no_reset_db:
        update_env_key(env_file, "RESET", "0")
        _info("RESET=0  (DB preserved)")

    if args.full_ingress:
        update_env_key(env_file, "FULL_INGRESS", "1")
        _info("FULL_INGRESS=1")
    elif args.no_full_ingress:
        update_env_key(env_file, "FULL_INGRESS", "0")
        _info("FULL_INGRESS=0")

    if args.admin_password:
        update_env_key(env_file, "ADMIN_PASSWORD", args.admin_password)
        _info("ADMIN_PASSWORD updated")


def print_admin_credentials(env_file: Path) -> None:
    env = read_env_file(env_file)
    pw = env.get("ADMIN_PASSWORD", "(not set)")
    print(f"\n  Admin login:  admin / {pw}")


def write_addr_file(
    cfg: dict,
    compose_file: Path,
    env_file: Path,
    project_name: str | None,
) -> None:
    """Write the server's reachable https URLs to ROOT_DIR/addr.txt after a
    successful up/restart, one per line: the local https listener, the
    tailnet URL (if configured) and the onion address (queried best-effort
    from the running stack — managed onions may publish a few seconds after
    startup; re-run the `onion` command later if the line is missing)."""
    nginx = cfg.get("nginx", {})
    https_port = nginx.get("https_port")
    ssl = _parse_ssl(cfg)
    urls: list[str] = []
    if https_port and ssl["mode"] != "none":
        urls.append(f"https://localhost:{https_port}/")
    ts_url = _tailscale_url(cfg)
    if ts_url:
        urls.append(ts_url.rstrip("/") + "/")
    tor = _parse_tor(cfg)
    if tor["enabled"]:
        name = cfg["deployment"]["name"]
        if tor["managed"]:
            onion_args = ["exec", "-T", f"web_{name}", "python", "manage.py", "nomad_onion"]
        else:
            onion_args = ["exec", "-T", "tor", "cat",
                          f"/var/lib/tor/{tor['service_dir']}/hostname"]
        cmd = ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file)]
        if project_name:
            cmd += ["-p", project_name]
        result = subprocess.run([*cmd, *onion_args], cwd=ROOT_DIR,
                                capture_output=True, text=True, check=False)
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        onion = lines[-1] if lines else ""
        if result.returncode == 0 and onion.endswith(".onion"):
            port = int(tor.get("onion_port") or 443)
            urls.append(f"https://{onion}/" if port == 443 else f"https://{onion}:{port}/")
    addr_file = ROOT_DIR / "addr.txt"
    addr_file.write_text("".join(u + "\n" for u in urls))
    _ok(f"Wrote addr.txt ({len(urls)} https URL(s))")


def print_urls(cfg: dict) -> None:
    nginx = cfg.get("nginx", {})
    http_port = nginx.get("http_port", 8080)
    https_port = nginx.get("https_port")
    ssl = _parse_ssl(cfg)
    print("\n  URLs:")
    print(f"    http://localhost:{http_port}/")
    if https_port and ssl["mode"] != "none":
        print(f"    https://localhost:{https_port}/")
    if cfg.get("services", {}).get("prometheus"):
        print("    http://localhost:9091/  (Prometheus)")
    if cfg.get("services", {}).get("grafana"):
        print("    http://localhost:3000/  (Grafana)")
    _gitea = cfg.get("services", {}).get("gitea")
    if _gitea:
        if https_port and ssl["mode"] != "none":
            print(f"    https://localhost:{https_port}/gitea/  (Gitea — staff SSO)")
        else:
            print(f"    http://localhost:{http_port}/gitea/  (Gitea — staff SSO)")
        _gitea_cfg = _gitea if isinstance(_gitea, dict) else {}
        if _gitea_cfg.get("ssh", True):
            _p = int(_gitea_cfg.get("ssh_port", 2222))
            _host = _public_base_url(cfg).split("://", 1)[-1]
            print(f"    ssh://git@{_host}:{_p}/<owner>/<repo>.git  (Gitea — git over SSH)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.dev and args.command not in ("up", "restart"):
        _die(
            f"--dev is only allowed for a local up/restart, not for '{args.command}'. "
            "A deployment must build from the vendored toto tree at the version "
            "pinned in requirements.toto.txt — drop --dev."
        )

    config_path = args.config
    if not config_path.is_absolute():
        config_path = (ROOT_DIR / config_path).resolve()

    print(f"\nDeploy  {config_path.name}")
    print("=" * 50)

    cfg = load_config(config_path)

    if args.command == "validate":
        errors = validate_config(cfg, config_path)
        if errors:
            _die(f"Config validation failed for {config_path.name}:", errors)
        _ok(f"{config_path.name} is valid")
        return

    errors = validate_config(cfg, config_path)
    if errors:
        _die(f"Config validation failed for {config_path.name}:", errors)

    dep = cfg["deployment"]
    name = dep["name"]
    target = _parse_target(cfg)
    env_file = ROOT_DIR / f".env.{name}"
    project_name = name if args.service else None

    # --- Tailscale pre-flight check ---
    if args.command not in ("validate", "env", "nginx", "config", "tor", "onion", "tailscale"):
        check_tailscale_config(cfg, target)

    # --- provision: SSH only, no local file generation needed ---
    if args.command == "provision":
        cmd_provision(cfg)
        return

    # --- renew: re-run certbot on the host (SSH for remote), no file generation ---
    if args.command == "renew":
        cmd_renew(cfg, force=args.force)
        return

    # --- dry-run: nginx conf preview ---
    if args.command == "nginx":
        print(f"\n# Generated nginx.conf for {name}\n")
        print(build_nginx_conf(cfg))
        return

    # --- dry-run: torrc preview ---
    if args.command == "tor":
        # Derive the HashedControlPassword from the same persisted cleartext the
        # web container will get (or a fresh one for a never-deployed stack).
        preview_env = build_env(cfg, read_env_file(env_file))
        print(f"\n# Generated torrc for {name}\n")
        print(build_torrc(cfg, control_password=preview_env.get("NOMAD_TOR_CONTROL_PASSWORD")))
        return

    # --- query: print the current .onion address ---
    if args.command == "onion":
        tor = _parse_tor(cfg)
        if not tor["enabled"]:
            _die("This config has no tor onion service (set tor.enabled: true).")
        # Managed onions have no HiddenServiceDir/hostname file — ask nomad (in the
        # web container) for the current address instead.
        if tor["managed"]:
            onion_args = ("exec", "-T", f"web_{name}", "python", "manage.py", "nomad_onion")
        else:
            hostname_path = f"/var/lib/tor/{tor['service_dir']}/hostname"
            onion_args = ("exec", "-T", "tor", "cat", hostname_path)
        if target["type"] != "local":
            remote_compose_run(target, name, *onion_args, project_name=project_name, check=False)
        else:
            compose_file = COMPOSE_DIR / name / "docker-compose.yaml"
            compose_run(compose_file, env_file, *onion_args, project_name=project_name, check=False)
        return

    # --- query: print the clearnet-over-tailnet connect URL (empty if not configured) ---
    if args.command == "tailscale":
        url = _tailscale_url(cfg)
        if url:
            print(url)
        return

    # --- dry-run: env preview ---
    if args.command == "env":
        existing = read_env_file(env_file)
        env = build_env(cfg, existing)
        print(f"\n# Generated .env for {name}  (existing secrets preserved)\n")
        for k, v in env.items():
            print(f"{k}={v}")
        return

    # --- generate files on disk ---
    COMPOSE_DIR.mkdir(exist_ok=True)
    stack_compose_dir = COMPOSE_DIR / name
    stack_compose_dir.mkdir(exist_ok=True)
    compose_file = stack_compose_dir / "docker-compose.yaml"

    compose_dict = build_compose(cfg)

    if args.command == "config":
        print(f"\n# Generated docker-compose for {name}\n")
        print(yaml.dump(compose_dict, default_flow_style=False, sort_keys=False))
        return

    with compose_file.open("w", encoding="utf-8") as f:
        yaml.dump(compose_dict, f, default_flow_style=False, sort_keys=False)
    _ok(f"Generated {compose_file.relative_to(ROOT_DIR)}")

    # Write generated nginx.conf
    nginx_conf_file = stack_compose_dir / "nginx.conf"
    nginx_conf_file.write_text(build_nginx_conf(cfg), encoding="utf-8")
    _ok(f"Generated {nginx_conf_file.relative_to(ROOT_DIR)}")

    # Build the env first (once — it mints secrets) so the torrc's
    # HashedControlPassword can be derived from the same cleartext control password.
    existing_env = read_env_file(env_file)
    new_env = build_env(cfg, existing_env)

    # Write generated torrc (only when a Tor onion service is configured)
    if _parse_tor(cfg)["enabled"]:
        torrc_file = stack_compose_dir / "torrc"
        torrc_file.write_text(
            build_torrc(cfg, control_password=new_env.get("NOMAD_TOR_CONTROL_PASSWORD")),
            encoding="utf-8",
        )
        _ok(f"Generated {torrc_file.relative_to(ROOT_DIR)}")

    # Write .env
    write_env_file(
        env_file,
        new_env,
        header=f"# Generated by deploy.py from {config_path.name}. Edit values here to override.",
    )
    _ok(f"Generated {env_file.name}")
    apply_switches(args, env_file)

    # SSL certs
    ensure_ssl_for_mode(cfg)

    # --smart: reuse the image when nothing baked into it changed.
    fingerprint_file = COMPOSE_DIR / name / "build.fingerprint"
    current_fingerprint = None
    if args.command in ("up", "restart") and not args.no_build:
        current_fingerprint = build_fingerprint(cfg)
        if args.smart:
            stored = fingerprint_file.read_text().strip() if fingerprint_file.exists() else None
            if stored == current_fingerprint:
                args.no_build = True
                _info("smart build: image inputs unchanged — reusing existing image")
            else:
                _info("smart build: image inputs changed — rebuilding")

    # (--no-build skips both for up/restart: nothing is rebuilt locally.)
    if args.command == "push" or (
            args.command in ("up", "restart") and not args.no_build):
        stage_toto_wheels(cfg, dev=args.dev)

    # --- push: rsync + remote docker compose ---
    if args.command == "push":
        # (--dev was refused before any wheel was built; see the guard in main.)
        cmd_push(cfg, compose_file, env_file,
                 detach=args.detach or bool(args.service),
                 project_name=project_name)
        return

    if target["type"] != "local":
        _warn(
            f"Target type is '{target['type']}' (non-local). "
            "Use 'provision' to set up the host, then 'push' to deploy."
        )

    # --- local docker compose commands ---
    if args.command == "up":
        if args.service:
            _info(f"Stopping any existing {name} service...")
            compose_run(compose_file, env_file, "down",
                        project_name=project_name, check=False)
        run_up(compose_file, env_file,
               detach=args.detach or bool(args.service),
               project_name=project_name,
               build=not args.no_build)
        if not args.no_build and current_fingerprint:
            fingerprint_file.write_text(current_fingerprint + "\n")
        print_urls(cfg)
        print_admin_credentials(env_file)
        write_addr_file(cfg, compose_file, env_file, project_name)

    elif args.command in ("down", "stop"):
        if args.service or args.command == "stop":
            if is_running(compose_file, env_file, name):
                _info(f"Stopping {name} service...")
                compose_run(compose_file, env_file, "down", project_name=name)
            else:
                _info(f"{name} service is not running")
        else:
            compose_run(compose_file, env_file, "down")

    elif args.command == "restart":
        compose_run(compose_file, env_file, "down", project_name=project_name, check=False)
        run_up(compose_file, env_file,
               detach=args.detach or bool(args.service),
               project_name=project_name,
               build=not args.no_build)
        if not args.no_build and current_fingerprint:
            fingerprint_file.write_text(current_fingerprint + "\n")
        print_urls(cfg)
        print_admin_credentials(env_file)
        write_addr_file(cfg, compose_file, env_file, project_name)

    elif args.command == "logs":
        compose_run(compose_file, env_file, "logs", "-f", project_name=project_name)


if __name__ == "__main__":
    main()
