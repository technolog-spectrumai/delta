"""Bring the configured stack up on this machine, via the host's own deploy.py.

The builder runs the per-host shim (``<host>/<host>/scripts/deploy.py``) rather
than the host's ``deploy_local.sh``, because the GUI has to own the flags: the
wrapper pins its own CONFIG profile and runs **attached**, which a window cannot
do. Everything here is pure — no Qt — so the argv construction and the guards
are unit-testable without a display or a docker daemon. The Qt side lives in
``worker.ProcessJob`` and ``app.DeployTab``.

Three facts about deploy.py shape this module, each verified against its source:

* ``up`` without ``-d`` is attached and never returns (``run_up``), so ``-d``
  is not a user-visible choice — :func:`build_argv` always emits it.
* ``apply_switches()`` runs *before* the command dispatch, so a ``--reset-db``
  sent along with ``down`` would arm a database wipe for the **next** boot.
  That is why :data:`SWITCHES_BY_COMMAND` is empty for everything but ``up``,
  and why the emptiness is asserted rather than assumed.
* a config outside ``HOST_ROOT`` is refused by deploy.py itself. We mirror the
  check so the failure surfaces in the GUI instead of as a child traceback.

One hazard this module does not close: two *different* stacks on the same host
share ``<host>/dist/``, and ``stage_toto_wheels`` clears stale wheels before it
rebuilds, so concurrent deploys of different projects on one host can still race
there. :func:`lock_path` is per-project, matching ``deploy/run.sh``; a per-host
second lock is the fix if that ever bites.
"""

from __future__ import annotations

import fcntl
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from validation import get_path

COMMANDS = ("up", "down", "logs")

# The remote pair, kept apart from COMMANDS on purpose: the GUI passes them
# none of the local switches (deploy.py's apply_switches runs for push too, so
# a --reset-db WOULD land in the .env the push ships — which is exactly why
# they are not offered), they REQUIRE a remote target where the local three
# refuse one, and their argv is deliberately minimal — the same `deploy.py
# <config> push` that deploy_server.sh has always run, so the GUI and the
# script stay byte-for-byte interchangeable. `push` rsyncs the repo to the
# server and rebuilds the stack there over SSH; `provision` does the
# first-time server setup (docker, tailscale, certbot).
#
# Push takes the same per-stack lock as a local up: both stage wheels into the
# shared <host>/dist, so overlapping a push with an up (or a second push) races
# there. deploy_server.sh itself takes no lock — the GUI refusing to overlap
# THAT is not something the flock can promise, only two lock-takers refuse
# each other.
REMOTE_COMMANDS = ("push", "provision")

# Which switches deploy.py will actually honour, per command. `down` and `logs`
# reach apply_switches() too, so a --reset-db there writes RESET=1 into the env
# file and wipes the database on the next boot — the emptiness of those two
# entries is load-bearing, not an oversight.
SWITCHES_BY_COMMAND: dict[str, frozenset[str]] = {
    "up": frozenset({"force_rebuild", "dev", "reset_db", "fake_data"}),
    "down": frozenset(),
    "logs": frozenset(),
}

SWITCH_FLAGS = {
    "dev": "--dev",
    "reset_db": "--reset-db",
    "fake_data": "--fake-data",
}

# Same order deploy_local.sh walks, minus its `command -v python3` tail: see
# _interpreter_tail below.
VENV_CANDIDATES = ("venv/bin/python", ".venv/bin/python", ".venv_test/bin/python")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


class DeployError(RuntimeError):
    """A problem worth showing the user verbatim."""


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

def interpreter(host_root: Path) -> Path:
    """Pick the interpreter to run deploy.py with.

    Mirrors deploy_local.sh's venv order. The tail differs deliberately: the
    wrapper falls back to a system ``python3``, which may lack PyYAML, whereas
    the builder's own interpreter is guaranteed to have both PyYAML and pip —
    and pip is needed, because stage_toto_wheels shells ``sys.executable -m pip
    wheel`` to stage the toto wheels.
    """
    for rel in VENV_CANDIDATES:
        candidate = host_root / rel
        if os.access(candidate, os.X_OK):
            return candidate
    return Path(sys.executable)


def shim(spec: Any) -> Path:
    """The host's deploy.py shim, which sets HOST_ROOT and runpy's the shared one."""
    candidate = spec.host_root / spec.host / "scripts" / "deploy.py"
    if candidate.is_file():
        return candidate
    shared = spec.host_root.parent / "scripts" / "deploy.py"
    if shared.is_file():
        # Equivalent as long as we set HOST_ROOT ourselves, which child_env does
        # — the shim only setdefault()s it.
        return shared
    raise DeployError(f"no deploy.py found for {spec.host} (looked in {candidate})")


def normalise_filename(name: str, default: str) -> str:
    name = (name or "").strip() or default
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"
    return name


def config_target(spec: Any, name: str) -> Path:
    """Where the deploy tab writes the config it is about to run."""
    return spec.configs_dir / normalise_filename(name, spec.default_filename)


def stack_name(spec: Any, cfg: dict) -> str:
    return str((cfg.get("deployment") or {}).get("name") or spec.host)


def describe(argv: list[str]) -> str:
    """The command as you would type it — logged so it can be pasted verbatim."""
    return shlex.join(argv)


def clean_line(text: str) -> str:
    """Strip ANSI and collapse a progress line to its last carriage return."""
    text = text.rstrip("\n")
    if "\r" in text:
        text = text.rsplit("\r", 1)[-1]
    return _ANSI.sub("", text)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def filter_switches(command: str, **switches: bool) -> dict[str, bool]:
    """Zero out switches the command cannot use, so UI and argv cannot drift."""
    allowed = SWITCHES_BY_COMMAND.get(command, frozenset())
    return {name: bool(value) and name in allowed for name, value in switches.items()}


def build_argv(
    spec: Any,
    config_path: Path,
    command: str,
    *,
    force_rebuild: bool = False,
    dev: bool = False,
    reset_db: bool = False,
    fake_data: bool = False,
    python: Path | None = None,
) -> list[str]:
    """Build the exact argv for one local deploy command."""
    if command not in COMMANDS:
        raise ValueError(f"{command!r} is not a local deploy command {COMMANDS}")

    _check_config_path(spec, config_path)

    requested = {"force_rebuild": force_rebuild, "dev": dev,
                 "reset_db": reset_db, "fake_data": fake_data}
    allowed = SWITCHES_BY_COMMAND[command]
    illegal = sorted(name for name, on in requested.items() if on and name not in allowed)
    if illegal:
        raise ValueError(
            f"{', '.join(illegal)} cannot be used with '{command}' — deploy.py "
            "applies env switches before the command runs, so they would take "
            "effect on the next boot instead"
        )

    py = python or interpreter(spec.host_root)
    argv = [str(py), str(shim(spec)), str(config_path), command, "--service"]
    if command == "up":
        # Attached is not an option for a window: it never returns, and the URL
        # and admin-credential lines only print on the detached path.
        argv.append("-d")
        if not force_rebuild:
            argv.append("--smart")
        for name, flag in SWITCH_FLAGS.items():
            if requested[name]:
                argv.append(flag)
    return argv


def _check_config_path(spec: Any, config_path: Path) -> None:
    if not config_path.is_absolute():
        # deploy.py resolves a relative path cwd-first, and a GUI's cwd is
        # nobody's business — always hand it an absolute path.
        raise ValueError(f"config path must be absolute, got {config_path}")
    if not config_path.is_relative_to(spec.host_root):
        raise ValueError(
            f"config {config_path} is not under {spec.host_root} — deploy.py "
            "refuses a config belonging to a different host"
        )


def build_remote_argv(
    spec: Any,
    config_path: Path,
    command: str,
    *,
    dry_run: bool = False,
    python: Path | None = None,
) -> list[str]:
    """Build the exact argv for one remote (SSH) deploy command.

    No ``--service``, no ``-d``, no build switches: deploy.py hardcodes the
    remote compose invocation (``up --build -d`` on the server) and refuses
    ``--dev`` for push, so there is no knob at all in this copy: delta's
    deploy.py fork predates ``--dry-run`` and its argparse rejects the flag, so
    the GUI does not offer the option and this refuses to fabricate it. Silently
    dropping it would turn "preview the push" into a real push.
    """
    if command not in REMOTE_COMMANDS:
        raise ValueError(f"{command!r} is not a remote deploy command {REMOTE_COMMANDS}")
    if dry_run:
        raise ValueError(
            "delta's deploy.py has no --dry-run; a preview push is not available"
        )

    _check_config_path(spec, config_path)

    py = python or interpreter(spec.host_root)
    argv = [str(py), str(shim(spec)), str(config_path), command]
    if dry_run:
        argv.append("--dry-run")
    return argv


def child_env(spec: Any, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the child needs to behave in a pipe."""
    env = dict(os.environ if base is None else base)
    env["HOST_ROOT"] = str(spec.host_root)
    # deploy.py never flushes, so its own progress lines would arrive long after
    # the docker output it inherits an fd for.
    env["PYTHONUNBUFFERED"] = "1"
    # It prints ✓ → ⚠ •, which a piped POSIX locale cannot encode.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def local_target_issue(cfg: dict) -> str | None:
    """Refuse a config aimed at a remote host.

    deploy.py only warns here and then deploys locally anyway, which is how you
    end up standing a production profile up on your laptop.
    """
    target = get_path(cfg, "target.type")
    if target in (None, "", "local"):
        return None
    return (
        f"target.type is '{target}', so this config describes a remote host. "
        "Deploying it here would bring the stack up on this machine instead. "
        "Use Push (deploy.py push) to deploy it to the server, or set the "
        "target to local."
    )


def remote_target_issue(cfg: dict) -> str | None:
    """Refuse a remote command for a config that does not describe a server.

    The mirror image of :func:`local_target_issue`: deploy.py's push and
    provision both die on ``target.type: local``, and an empty ``target.host``
    dies later and uglier, inside rsync.
    """
    target = get_path(cfg, "target.type")
    if target in (None, "", "local"):
        return (
            "this config's target is local, so there is no server to push to. "
            "Set target.type (ovh/ec2/device) and target.host on the Network "
            "tab first."
        )
    host = str(get_path(cfg, "target.host") or "").strip()
    if not host:
        return (
            f"target.type is '{target}' but target.host is empty — deploy.py "
            "needs the server's IP or hostname to rsync to."
        )
    return None


def remote_summary(cfg: dict) -> str:
    """One line saying where a push would land — deploy.py's own semantics."""
    target = get_path(cfg, "target.type")
    if target in (None, "", "local"):
        return "— (local target)"
    t = cfg.get("target") or {}
    host = str(t.get("host", "") or "").strip() or "?"
    # dict.get defaults, EXACTLY as deploy.py's _parse_target applies them: an
    # absent key gets the default, a present key keeps its value — even "".
    # Coercing an empty value to the default here would make this label (and
    # the push confirmation built from it) claim /srv/toto while rsync runs
    # against the server's filesystem root. Empties render as-is, visibly
    # wrong, and remote_blockers refuses them.
    user = str(t.get("user", "ubuntu"))
    remote_dir = str(t.get("remote_dir", "/srv/toto"))
    return f"{user}@{host}:{remote_dir} ({target})"


def _tailscale_issue(
    cfg: dict, which: Callable[[str], str | None]
) -> str | None:
    """The local tailscale CLI requirement, shared by both preflights.

    Local up resolves the tailnet IP on this machine, and so does a PUSH:
    deploy.py builds the compose file locally before rsyncing, and a config
    with ``nginx.bind: tailscale`` runs ``tailscale ip -4`` right there.
    """
    wants_tailscale = (
        bool((cfg.get("tailscale") or {}).get("enabled"))
        or get_path(cfg, "ssl.mode") == "tailscale"
        or get_path(cfg, "nginx.bind") == "tailscale"
    )
    if wants_tailscale and which("tailscale") is None:
        return (
            "this config uses tailscale, but the tailscale CLI is not installed "
            "— deploy.py would crash resolving the tailnet address."
        )
    return None


def remote_blockers(
    spec: Any,
    cfg: dict,
    command: str = "push",
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Everything that would make a push or provision fail before it starts.

    Deliberately NOT the docker preflight: the image builds on the server, so
    local docker is irrelevant. What the local side does need is ssh (plus
    rsync for a push — provision never copies anything), a key that actually
    exists when one is named — BatchMode=yes means a missing key fails without
    ever asking for a password — and a config that deploy.py's validate will
    not refuse outright.
    """
    found: list[str] = []

    issue = remote_target_issue(cfg)
    if issue:
        found.append(issue)

    # deploy.py's _parse_target only applies its defaults for a MISSING key; a
    # key that is present but empty stays empty. An empty remote_dir is the
    # worst case: rsync --delete against `user@host:/` — the server's
    # filesystem root. Refuse rather than guess.
    t = cfg.get("target") or {}
    for field, consequence in (
        ("user", "ssh would connect as an empty user and fail"),
        ("remote_dir", "rsync --delete would run against the server's "
                       "filesystem root"),
    ):
        if field in t and not str(t.get(field) or "").strip():
            found.append(
                f"target.{field} is present but empty — deploy.py only applies "
                f"its default when the key is absent, so {consequence}. Remove "
                "the key or give it a value."
            )

    if which("ssh") is None:
        found.append("ssh is not on PATH — install an OpenSSH client.")
    if command == "push" and which("rsync") is None:
        found.append("rsync is not on PATH — push copies the repo with rsync.")

    ts = _tailscale_issue(cfg, which)
    if ts:
        found.append(ts)

    key = str(get_path(cfg, "target.key") or "").strip()
    if key and not Path(key).expanduser().exists():
        found.append(
            f"target.key points at {key}, which does not exist. SSH runs with "
            "BatchMode=yes, so with no usable key it fails instead of asking "
            "for a password. Fix the path, or clear it to use the ssh agent."
        )

    if str((cfg.get("env") or {}).get("RESET", "")) == "1":
        # deploy.py validate refuses this pairing; saying it here beats a child
        # traceback after the config is already written.
        found.append(
            "this config sets RESET=1, which deploy.py refuses for a remote "
            "target — it would drop the server's database on every boot."
        )
    return found


def docker_preflight(
    *,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Check docker before the child does, because the child checks nothing.

    deploy.py invokes ``docker compose`` directly with no ``which`` guard, so a
    machine without it dies on an uncaught FileNotFoundError. ``compose
    version`` answers without contacting the daemon, so ``info`` is the one that
    proves the daemon is up and this user may talk to it.
    """
    if which("docker") is None:
        return ["docker is not on PATH — install docker and the compose plugin."]

    checks = (
        (["docker", "compose", "version"],
         "`docker compose` is not available — install the docker compose plugin."),
        (["docker", "info", "--format", "{{.ServerVersion}}"],
         "the docker daemon is not reachable — start it, or add yourself to the "
         "docker group and log back in."),
    )
    found: list[str] = []
    for cmd, message in checks:
        try:
            result = runner(cmd, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            found.append(f"{message} ({exc})")
            continue
        if result.returncode != 0:
            found.append(message)
    return found


def blockers(
    spec: Any,
    cfg: dict,
    *,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Everything that would make this deploy fail before it starts."""
    found = docker_preflight(runner=runner, which=which)

    issue = local_target_issue(cfg)
    if issue:
        found.append(issue)

    ts = _tailscale_issue(cfg, which)
    if ts:
        found.append(ts)
    return found


def destructive_notes(cfg: dict, *, reset_db: bool) -> list[str]:
    """Why this deploy needs a second confirmation, if it does."""
    env = cfg.get("env") or {}
    notes: list[str] = []
    config_resets = str(env.get("RESET", "")) == "1"

    if reset_db:
        notes.append("--reset-db wipes the database and re-seeds it on startup.")
    if config_resets:
        # Aurelian's local preset ships this, so an unticked box is no defence.
        notes.append(
            "this config sets RESET=1, so the database is dropped and re-seeded "
            "every time the stack starts — with or without the checkbox."
        )
    if (reset_db or config_resets) and str(env.get("FULL_INGRESS", "")) == "1":
        notes.append("FULL_INGRESS=1 will then seed demo data into the fresh database.")
    return notes


# ---------------------------------------------------------------------------
# The build lock
# ---------------------------------------------------------------------------

def lock_path(spec: Any, cfg: dict) -> Path:
    """The lock deploy/run.sh uses, byte for byte.

    Sharing the path is the point: it makes the GUI and the shell script refuse
    to overlap, rather than each being merely self-consistent. Two `up --build`
    runs against one compose project deadlock on the buildkit lock.
    """
    tmp = Path(os.environ.get("TMPDIR", "/tmp"))
    return tmp / f"{spec.host}-deploy-{stack_name(spec, cfg)}.lock"


class DeployLock:
    """A non-blocking flock, released by the kernel if we die holding it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError:
            os.close(fd)
            # A filesystem that cannot flock should not block a deploy.
            return True
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            os.close(self._fd)   # closing the fd drops the lock
        except OSError:
            pass
        self._fd = None
