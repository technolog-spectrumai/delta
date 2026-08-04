#!/usr/bin/env python3
"""Tests for the Delta Builder.

    QT_QPA_PLATFORM=offscreen tools/delta_builder/venv/bin/python tools/test_builder.py

stdlib unittest run as a script, mirroring the portal's tools/test_builder.py and
delta's own scripts/test_deploy.py — this repo has no pytest outside the vendored
library, and a GUI test should not be the thing that introduces one.

The load-bearing test is PresetTests: every preset must render YAML that
round-trips and that delta's REAL deploy.py validator accepts. That is what
catches drift when a rule is added to validate_config.

CapabilityTests are delta's own: they assert the flags cannot express a broken
host — every capability on its own must produce a config that validates, and the
authoring bundle must stay a bundle (cyprian without memo is a check error, so
the builder must not be able to write it).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_TOP = Path(__file__).resolve().parent.parent
COMMON = REPO_TOP / "tools" / "common"
BUILDER = REPO_TOP / "tools" / "delta_builder"
sys.path.insert(0, str(COMMON))
sys.path.insert(0, str(BUILDER))

# deploy_bridge reads HOST_ROOT at import time, exactly as main.py does.
os.environ.setdefault("HOST_ROOT", str(REPO_TOP))


def _load_spec_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_spec_delta", BUILDER / "spec.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_spec_delta"] = module
    spec.loader.exec_module(module)
    return module


def _load_spec():
    return _load_spec_module().build_spec(REPO_TOP)


class PresetTests(unittest.TestCase):
    """Every preset must produce a config delta's own deploy tool accepts."""

    def test_presets_render_and_validate(self):
        import deploy_bridge
        import validation
        import yaml_render

        spec = _load_spec()
        self.assertTrue(spec.presets, "the builder ships no presets")
        for preset in spec.presets:
            with self.subTest(preset=preset.key):
                cfg = preset.config
                text = yaml_render.render(cfg, header=spec.header,
                                          comments=spec.comments)
                yaml_render.verify_roundtrip(text, cfg)

                issues = deploy_bridge.validate(
                    spec.host_root, cfg, spec.configs_dir / "generated.yaml")
                issues += validation.check_common(cfg)
                issues += validation.check_flags(cfg.get("env") or {})
                if spec.extra_checks:
                    issues += spec.extra_checks(cfg)

                blocking = validation.errors(issues)
                # The server preset legitimately ships with the fields the user
                # must fill (Let's Encrypt email, domain, target host) blank.
                if preset.key == "server":
                    blocking = [
                        i for i in blocking
                        if not i.path.startswith(("ssl.email", "ssl.domain",
                                                  "target.host", "env.PLATFORM_DOMAIN",
                                                  "env.ALLOWED_HOSTS", "cert."))
                    ]
                self.assertEqual([str(i) for i in blocking], [],
                                 f"preset {preset.key} does not validate")

    def test_every_preset_keeps_the_worker(self):
        """delta's beat recomputes recommendations nightly — no preset may drop it.

        The upstream derive_config rebuilds services: from the feature closure
        alone, which would delete a worker no capability claims. delta's vendored
        copy seeds it from the config instead; this is that patch's regression
        test.
        """
        for preset in _load_spec().presets:
            with self.subTest(preset=preset.key):
                self.assertTrue((preset.config.get("services") or {}).get("celery"),
                                f"{preset.key} lost the celery worker")

    def test_no_preset_asks_for_asgi(self):
        # delta ships no asgi.py; a preset that derived one would produce a
        # container that cannot start.
        for preset in _load_spec().presets:
            with self.subTest(preset=preset.key):
                self.assertEqual(preset.config.get("server"), "wsgi")


class CapabilityTests(unittest.TestCase):
    """The flags must not be able to describe a host that will not run."""

    def test_every_capability_alone_produces_a_valid_config(self):
        import app as app_module
        import deploy_bridge
        import validation

        module = _load_spec_module()
        spec = _load_spec()
        for cap in spec.capabilities:
            with self.subTest(capability=cap.key):
                cfg = module._preset_config(REPO_TOP, (cap.key,), {})
                issues = deploy_bridge.validate(
                    spec.host_root, cfg, spec.configs_dir / "generated.yaml")
                issues += validation.check_common(cfg)
                issues += validation.check_flags(cfg.get("env") or {})
                issues += spec.extra_checks(cfg)
                self.assertEqual([str(i) for i in validation.errors(issues)], [],
                                 f"capability {cap.key} alone does not validate")
                self.assertIsNotNone(app_module.is_selected(cap, cfg))

    def test_the_authoring_bundle_is_indivisible(self):
        """One flag, four apps — so 'documents without presentations', which is a
        cyprian.E001 system-check error, cannot be expressed by the UI at all."""
        spec = _load_spec()
        caps = {c.key: c for c in spec.capabilities}
        self.assertIn("authoring", caps)
        self.assertEqual(caps["authoring"].flags, ("BUILD_AUTHORING",))
        # No capability may name memo/cyprian/primula/editor separately.
        for cap in spec.capabilities:
            for flag in cap.flags:
                self.assertNotIn(flag, ("BUILD_MEMO", "BUILD_CYPRIAN",
                                        "BUILD_PRIMULA", "BUILD_EDITOR"),
                                 f"{cap.key} splits the authoring bundle")

    def test_the_lean_preset_turns_capabilities_off_explicitly(self):
        """Subtractive flags: 'off' must be written, not merely omitted, or the
        default (on) silently wins and the lean profile is not lean."""
        lean = {p.key: p.config for p in _load_spec().presets}["lean"]
        env = lean.get("env") or {}
        for flag in ("BUILD_AUTHORING", "BUILD_LIBRARY", "BUILD_SUBSCRIPTIONS",
                     "BUILD_VOD", "BUILD_SOCIAL_LOGIN"):
            self.assertEqual(env.get(flag), "0", f"{flag} is not explicitly off")

    def test_the_core_is_not_offered_as_a_toggle(self):
        """Nothing that cannot actually be switched off may appear as a tick."""
        offered = {flag for cap in _load_spec().capabilities for flag in cap.flags}
        for forbidden in ("BUILD_ACADEMY", "BUILD_QUIZZES", "BUILD_COMPETENCE",
                          "BUILD_PALIMPSEST", "BUILD_QUOTA", "BUILD_BACKOFFICE"):
            self.assertNotIn(forbidden, offered)


class HostSpecTests(unittest.TestCase):
    def test_spec_points_at_this_repo(self):
        spec = _load_spec()
        self.assertEqual(spec.host, "delta")
        self.assertTrue(spec.icon.is_file(), f"missing icon: {spec.icon}")
        self.assertTrue(spec.configs_dir.is_dir(),
                        f"missing configs dir: {spec.configs_dir}")

    def test_the_vendored_deploy_tool_is_delta_s_own(self):
        """The standalone-repo patch: load_deploy must find delta/scripts/deploy.py
        and not go looking for a monorepo sibling."""
        import deploy_bridge

        module = deploy_bridge.load_deploy(REPO_TOP)
        self.assertIn("delta", str(Path(module.__file__)).lower())


if __name__ == "__main__":
    unittest.main(verbosity=1)
