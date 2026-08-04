# tools/common — the builder framework, vendored

This is delta's copy of the shared PyQt builder library that the portal monorepo
keeps at `portal/tools/common/`. It is vendored for the same reason
`vendor/toto_libs` is: **delta is a standalone repo**, and a builder that only
worked when a sibling checkout happened to sit next to it would not be a builder
delta owns.

Like the toto-works trim, an upstream improvement arrives here by being **copied
in**, not by a subtree pull — and the patches below have to be re-applied when it
does. Keep this list current; it is the diff.

## What was patched on the way in

1. **`deploy_bridge.load_deploy` — where deploy.py lives.** Upstream hardcodes
   `host_root.parent/"scripts"/deploy.py` (the monorepo shape) and raises
   "is this host inside the monorepo?" otherwise. Delta's fork lives at
   `delta/scripts/deploy.py`, so the lookup now tries the standalone layout
   first and the monorepo one second — the same order `deployer.shim()` already
   used, so the GUI and the tool it drives can never resolve to different files.

2. **The Package tab is gone**, along with `packager.py` and `installer.py`.
   The packager imports `deploy.PAYLOAD_EXCLUDES`, which delta's older fork does
   not define, and the generated `install.sh` calls `deploy/run.sh` and
   `deploy/issue_letsencrypt.sh`, neither of which exists in this repo. A tab
   that cannot work is worse than a tab that is not there.

3. **`--dry-run` is refused, not ignored.** Delta's deploy.py predates the flag
   and its argparse rejects it. `deployer.build_remote_argv` now raises rather
   than dropping it silently, and the Remote tab's "Preview the push only"
   checkbox was removed — a checkbox promising a preview while performing a real
   push is the worst possible failure here.

4. **`validation.RESERVED_PORTS` is delta's.** 8082/8443 were listed as "delta"
   upstream, which would make delta's own presets warn about themselves. They
   are removed; the other hosts' ports stay listed, because a developer commonly
   runs a portal stack beside this one, and delta's gate boot port (8767) was
   added.

5. **`derive_config` keeps what the config already declares.** Upstream rebuilds
   the `services:` block from the toto.features closure alone. Delta's celery
   worker and beat are part of the host (the recommendation matrix is recomputed
   nightly) and no capability claims them, so they would have been deleted from
   every generated config. It now seeds services from the incoming config and
   layers derived ones on top. For the same reason a host that pins
   `server: wsgi` — delta ships no `asgi.py` — keeps it instead of having the
   mode derived.

## Layout

`tools/common` is not an importable package (no `__init__.py`); modules import
each other by bare name, so the directory itself goes on `sys.path`. That is what
`tools/delta_builder/main.py` does, and `tools/test_builder.py` mirrors it.
