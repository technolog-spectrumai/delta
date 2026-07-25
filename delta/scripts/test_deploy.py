#!/usr/bin/env python3
"""Unit tests for deploy.py generation logic (no Django, no docker).

Run directly:  python delta/scripts/test_deploy.py
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deploy  # noqa: E402


def _cfg(services: dict | None = None) -> dict:
    return {
        "deployment": {"name": "testy", "project_dir": "delta"},
        "env": {},
        "services": services or {},
    }


class GiteaEnvTests(unittest.TestCase):
    def test_env_keys_set_when_enabled(self):
        env = deploy.build_env(_cfg({"gitea": True}), existing={})
        self.assertEqual(env["GITEA_ENABLED"], "1")
        self.assertEqual(env["GITEA_URL"], "/gitea/")
        self.assertTrue(env["GITEA_OIDC_CLIENT_SECRET"])
        # Hyphenated alias, not the web_testy service name — Django rejects
        # underscore Host headers before ALLOWED_HOSTS is consulted.
        self.assertEqual(
            env["GITEA_OIDC_DISCOVERY_URL"],
            "http://web-testy:8000/.well-known/openid-configuration-internal",
        )

    def test_env_keys_absent_when_disabled(self):
        env = deploy.build_env(_cfg(), existing={})
        for key in ("GITEA_ENABLED", "GITEA_URL", "GITEA_OIDC_CLIENT_SECRET",
                    "ALLOWED_HOSTS_EXTRA"):
            self.assertNotIn(key, env)

    def test_internal_alias_whitelisted_for_internal_sso_callers(self):
        for svc in ("gitea", "grafana"):
            env = deploy.build_env(_cfg({svc: True}), existing={})
            self.assertEqual(env["ALLOWED_HOSTS_EXTRA"], "web-testy", svc)

    def test_existing_secret_is_preserved(self):
        env = deploy.build_env(
            _cfg({"gitea": True}),
            existing={"GITEA_OIDC_CLIENT_SECRET": "stable-secret"},
        )
        self.assertEqual(env["GITEA_OIDC_CLIENT_SECRET"], "stable-secret")

    def test_gitvault_service_env(self):
        # portal-svc credentials + internal REST base for toto.gitvault.
        env = deploy.build_env(_cfg({"gitea": True}), existing={})
        self.assertEqual(env["GITEA_INTERNAL_URL"], "http://gitea:3000")
        self.assertTrue(env["GITEA_SVC_PASSWORD"])
        env2 = deploy.build_env(
            _cfg({"gitea": True}), existing={"GITEA_SVC_PASSWORD": "stable-pw"}
        )
        self.assertEqual(env2["GITEA_SVC_PASSWORD"], "stable-pw")

    def test_provision_sidecar_gets_svc_password(self):
        compose = deploy.build_compose(_cfg({"gitea": True}))
        env = compose["services"]["gitea_provision"]["environment"]
        self.assertIn("GITEA_SVC_PASSWORD=${GITEA_SVC_PASSWORD}", env)

    def test_config_env_secret_wins(self):
        cfg = _cfg({"gitea": True})
        cfg["env"]["GITEA_OIDC_CLIENT_SECRET"] = "explicit"
        env = deploy.build_env(cfg, existing={"GITEA_OIDC_CLIENT_SECRET": "old"})
        self.assertEqual(env["GITEA_OIDC_CLIENT_SECRET"], "explicit")


class GiteaNginxTests(unittest.TestCase):
    def test_location_present_when_enabled(self):
        conf = deploy.build_nginx_conf(_cfg({"gitea": True}))
        # ^~ short-circuits regex locations — without it the `~ /\.ht` deny-all
        # would 403 repo files named .ht* under /gitea/.
        self.assertIn("location ^~ /gitea/ {", conf)
        self.assertIn("client_max_body_size 512M;", conf)
        # The official strip-prefix recipe: raw-URI restore + prefix strip,
        # forwarded via variable proxy_pass.
        self.assertIn("rewrite ^ $request_uri;", conf)
        self.assertIn("rewrite ^/gitea(/.*) $1 break;", conf)
        self.assertIn("proxy_pass $backend$1;", conf)

    def test_location_absent_when_disabled(self):
        conf = deploy.build_nginx_conf(_cfg())
        self.assertNotIn("/gitea/", conf)

    def test_location_present_in_plain_http_server(self):
        cfg = _cfg({"gitea": True})
        cfg["ssl"] = {"mode": "none"}
        conf = deploy.build_nginx_conf(cfg)
        self.assertIn("location ^~ /gitea/ {", conf)


class GiteaComposeTests(unittest.TestCase):
    def test_services_and_volume_present(self):
        compose = deploy.build_compose(_cfg({"gitea": True}))
        services = compose["services"]
        self.assertIn("gitea", services)
        self.assertIn("gitea_provision", services)
        self.assertIn("gitea_data", compose["volumes"])
        self.assertIn("gitea_data:/data", services["gitea"]["volumes"])

    def test_absent_when_disabled(self):
        compose = deploy.build_compose(_cfg())
        self.assertNotIn("gitea", compose["services"])
        self.assertNotIn("gitea_data", compose["volumes"])

    def test_provision_sidecar_waits_for_gitea_and_web(self):
        compose = deploy.build_compose(_cfg({"gitea": True}))
        deps = compose["services"]["gitea_provision"]["depends_on"]
        self.assertEqual(deps["gitea"], {"condition": "service_healthy"})
        self.assertEqual(deps["web_testy"], {"condition": "service_healthy"})

    def test_gitea_boots_after_web(self):
        # Gitea re-registers persisted OAuth sources at startup, fetching the
        # discovery URL from the web container — web must be up first.
        compose = deploy.build_compose(_cfg({"gitea": True}))
        deps = compose["services"]["gitea"]["depends_on"]
        self.assertEqual(deps["web_testy"], {"condition": "service_healthy"})

    def test_root_url_uses_platform_domain_and_scheme(self):
        cfg = _cfg({"gitea": True})
        cfg["env"]["PLATFORM_DOMAIN"] = "portal.example.com"
        compose = deploy.build_compose(cfg)
        env = compose["services"]["gitea"]["environment"]
        self.assertIn("GITEA__server__ROOT_URL=https://portal.example.com/gitea/", env)

        # ssl-none is only valid with an explicit http:// scheme (validate_config
        # rejects the scheme-less combo) — the explicit scheme passes through, so
        # ROOT_URL agrees with the redirect URI sso_master registers.
        cfg["ssl"] = {"mode": "none"}
        cfg["env"]["PLATFORM_DOMAIN"] = "http://portal.example.com"
        compose = deploy.build_compose(cfg)
        env = compose["services"]["gitea"]["environment"]
        self.assertIn("GITEA__server__ROOT_URL=http://portal.example.com/gitea/", env)

    def test_web_service_has_hyphenated_alias(self):
        compose = deploy.build_compose(_cfg({"gitea": True}))
        nets = compose["services"]["web_testy"]["networks"]
        self.assertEqual(nets["testy_net"]["aliases"], ["web-testy"])

    def test_internal_urls_use_hyphenated_alias(self):
        compose = deploy.build_compose(_cfg({"gitea": True, "grafana": True}))
        gf_env = compose["services"]["grafana"]["environment"]
        self.assertIn("GF_AUTH_GENERIC_OAUTH_TOKEN_URL=http://web-testy:8000/sso/token/", gf_env)
        self.assertIn("GF_AUTH_GENERIC_OAUTH_API_URL=http://web-testy:8000/sso/userinfo/", gf_env)
        # No stray underscore hostnames in any URL handed to a sibling container.
        for e in gf_env + compose["services"]["gitea"]["environment"]:
            self.assertNotIn("web_testy", e)

    def test_sso_only_login_config(self):
        compose = deploy.build_compose(_cfg({"gitea": True}))
        env = compose["services"]["gitea"]["environment"]
        self.assertIn("GITEA__service__ENABLE_PASSWORD_SIGNIN_FORM=false", env)
        self.assertIn("GITEA__service__ALLOW_ONLY_EXTERNAL_REGISTRATION=true", env)
        self.assertIn("GITEA__oauth2_client__ENABLE_AUTO_REGISTRATION=true", env)
        # auto, not login: a RESET=1 portal wipe re-mints every user's `sub`,
        # and `login` would strand users on the link-account form (no local
        # passwords exist); `auto` re-links by username/email and signs in.
        self.assertIn("GITEA__oauth2_client__ACCOUNT_LINKING=auto", env)
        # oauth2_client.OPENID_CONNECT_SCOPES would override the per-source
        # --scopes set by provision_oauth.sh — must never be set here.
        self.assertFalse(any("OPENID_CONNECT_SCOPES" in e for e in env))


class GiteaSshTests(unittest.TestCase):
    """Git-over-SSH is on by default when gitea is enabled (Gitea's built-in SSH
    server, published as a direct container port since nginx is HTTP-only)."""

    def _gitea(self, services):
        cfg = _cfg(services)
        cfg["env"]["PLATFORM_DOMAIN"] = "portal.example.com"
        return deploy.build_compose(cfg)["services"]["gitea"]

    def test_ssh_enabled_by_default(self):
        gitea = self._gitea({"gitea": True})
        env = gitea["environment"]
        self.assertIn("GITEA__server__DISABLE_SSH=false", env)
        self.assertIn("GITEA__server__START_SSH_SERVER=true", env)
        self.assertIn("GITEA__server__SSH_DOMAIN=portal.example.com", env)
        self.assertIn("GITEA__server__SSH_PORT=2222", env)
        self.assertIn("GITEA__server__SSH_LISTEN_PORT=2222", env)
        # Directly published to the host (nginx can't carry SSH).
        self.assertIn("2222:2222", gitea["ports"])
        # The loopback debug port is preserved.
        self.assertIn("127.0.0.1:3300:3000", gitea["ports"])

    def test_ssh_can_be_disabled(self):
        gitea = self._gitea({"gitea": {"ssh": False}})
        env = gitea["environment"]
        self.assertIn("GITEA__server__DISABLE_SSH=true", env)
        self.assertFalse(any("START_SSH_SERVER" in e for e in env))
        self.assertFalse(any(":2222" in p for p in gitea["ports"]))

    def test_ssh_port_override(self):
        gitea = self._gitea({"gitea": {"ssh_port": 2223}})
        env = gitea["environment"]
        self.assertIn("GITEA__server__SSH_PORT=2223", env)
        self.assertIn("GITEA__server__SSH_LISTEN_PORT=2223", env)
        self.assertIn("2223:2223", gitea["ports"])

    def test_ssh_publish_honors_nginx_bind(self):
        cfg = _cfg({"gitea": True})
        cfg["env"]["PLATFORM_DOMAIN"] = "portal.example.com"
        cfg["nginx"] = {"bind": "127.0.0.1"}
        gitea = deploy.build_compose(cfg)["services"]["gitea"]
        self.assertIn("127.0.0.1:2222:2222", gitea["ports"])

    def test_dict_form_still_enables_service(self):
        # A truthy dict must enable gitea + its volume, like `gitea: true`.
        compose = deploy.build_compose(
            {**_cfg({"gitea": {"ssh_port": 2222}}), "env": {"PLATFORM_DOMAIN": "x.example.com"}}
        )
        self.assertIn("gitea", compose["services"])
        self.assertIn("gitea_data", compose["volumes"])


class CeleryEntrypointTests(unittest.TestCase):
    def test_celery_containers_bypass_init_entrypoint(self):
        # The image ENTRYPOINT (entrypoint.sh) runs init_platform + ingress_all;
        # only web may do that. A bare `command` would run it in every celery
        # container — with RESET=1 the concurrent wipes race (DROP SCHEMA dies
        # in one container) and clobber web's freshly-seeded DB.
        compose = deploy.build_compose(_cfg({"celery": True}))
        for svc in ("celery_worker", "celery_beat"):
            s = compose["services"][svc]
            self.assertEqual(s["entrypoint"][0], "celery", svc)
            self.assertNotIn("command", s, svc)


class GiteaValidateConfigTests(unittest.TestCase):
    """SSO is the only way into Gitea, and redirect URIs are exact-matched —
    validate_config must reject configs where deploy.py's public base and the
    registered redirect URI would drift."""

    @staticmethod
    def _gitea_errors(cfg):
        errors = deploy.validate_config(cfg, Path("dummy.yaml"))
        return [e for e in errors if "gitea" in e]

    def test_requires_platform_domain(self):
        errs = self._gitea_errors(_cfg({"gitea": True}))
        self.assertTrue(any("PLATFORM_DOMAIN must be set" in e for e in errs))

    def test_happy_path_has_no_gitea_errors(self):
        cfg = _cfg({"gitea": True})
        cfg["env"]["PLATFORM_DOMAIN"] = "portal.example.com"
        self.assertEqual(self._gitea_errors(cfg), [])

    def test_ssl_none_needs_explicit_http_scheme(self):
        cfg = _cfg({"gitea": True})
        cfg["env"]["PLATFORM_DOMAIN"] = "portal.example.com"
        cfg["ssl"] = {"mode": "none"}
        errs = self._gitea_errors(cfg)
        self.assertTrue(any("explicit http://" in e for e in errs))
        # Explicit scheme makes the combo consistent → accepted.
        cfg["env"]["PLATFORM_DOMAIN"] = "http://portal.example.com"
        self.assertEqual(self._gitea_errors(cfg), [])

    def test_ssl_domain_must_agree_with_platform_domain(self):
        cfg = _cfg({"gitea": True})
        cfg["env"]["PLATFORM_DOMAIN"] = "www.example.com"
        cfg["ssl"] = {"mode": "letsencrypt", "email": "x@example.com", "domain": "example.com"}
        errs = self._gitea_errors(cfg)
        self.assertTrue(any("must agree" in e for e in errs))
        cfg["ssl"]["domain"] = "www.example.com"
        self.assertEqual(self._gitea_errors(cfg), [])


class TotoManifestTests(unittest.TestCase):
    """The toto pin this host deploys, and the gate that enforces it."""

    def test_manifest_pins_are_exact_and_lockstep(self):
        manifest = deploy.read_toto_manifest()
        self.assertTrue(manifest.names, "requirements.toto.txt declares no packages")
        self.assertRegex(manifest.version, r"^\d+\.\d+$")
        for name in manifest.names:
            self.assertEqual(manifest.pins[name], manifest.version)

    def test_manifest_covers_every_toto_app_this_host_installs(self):
        """A package missing from the manifest is an ImportError at boot."""
        manifest = deploy.read_toto_manifest()
        if deploy.TOTO_SRC is None:
            self.skipTest("no toto suite checkout to resolve app ownership")
        from toto.versioning import app_dir

        settings = (deploy.ROOT_DIR / "delta" / "delta" / "settings.py").read_text()
        # INSTALLED_APPS entries only: "toto.<app>" with at most a trailing
        # comment (context processors and middleware paths have more dotted
        # parts; many delta entries carry `# ...` annotations).
        apps = sorted({
            match.group(1)
            for match in re.finditer(r'^\s*"toto\.(\w+)",\s*(?:#.*)?$', settings, re.MULTILINE)
        })
        self.assertTrue(apps, "found no toto apps in settings.py")
        for app in apps:
            if (deploy.ROOT_DIR / "delta" / "toto" / app).is_dir():
                continue  # host-carried portion (the six education apps) — versions with delta
            owner = app_dir(deploy.TOTO_SRC, app).parents[2].name
            self.assertIn(owner, manifest.names, f"toto.{app} needs {owner}")

    def test_stale_wheel_names_are_cleaned(self):
        """Wheel files spell toto-base as toto_base; both globs must be swept."""
        import inspect

        source = inspect.getsource(deploy.stage_toto_wheels)
        self.assertIn('glob("toto_*.whl")', source)
        self.assertIn('glob("toto-*.whl")', source)

    def test_fingerprint_covers_every_staged_wheel(self):
        import inspect

        source = inspect.getsource(deploy.build_fingerprint)
        self.assertIn('glob("toto_*.whl")', source)


if __name__ == "__main__":
    unittest.main()
