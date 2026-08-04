"""Render a deploy config as YAML that reads like the hand-written ones.

Not ``yaml.safe_dump``. The configs in ``<host>/<host>/deploy/configs/`` carry
their explanation in comments — what a profile is for, why a flag is on, which
command brings it up — and several knobs are documented nowhere else. A dump
would strip all of that and alphabetise the keys, producing a file nobody can
safely hand-edit afterwards. So sections are emitted in the house order with
their comments, and optional blocks are simply not emitted when unused.

House style, taken from the seven existing configs and enforced here:

* two-space indent, blank line between top-level sections
* key order: deployment, server, target, ssl, env, admin, services, nginx, cert
* ``BUILD_*``/``INSTALL_*`` are **quoted strings** ``"1"``/``"0"`` — only the
  exact string "1" is true to ``toto.features``, so a YAML boolean would read
  as false (``str(True) == "True"``)
* ``services:`` values are **bare booleans** (plain truthiness in deploy.py)
* nginx ports are **bare ints**
* trailing ``# why`` comments on the fields that carry them today
"""

from __future__ import annotations

import json
from typing import Any

import yaml

# The order deploy.py's most-maintained configs use.
SECTION_ORDER = (
    "deployment", "server", "target", "ssl", "env",
    "admin", "services", "nginx", "cert",
)


def scalar(value: Any) -> str:
    """Emit a scalar, quoting only when it would otherwise change meaning.

    Bare when YAML round-trips it to the identical string; JSON-quoted
    otherwise (JSON's string escapes are a subset of YAML's double-quoted
    style). Booleans and ints are emitted natively.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if text != text.strip() or text.lstrip().startswith("#"):
        return json.dumps(text)
    try:
        if yaml.safe_load(text) == text:
            return text
    except yaml.YAMLError:
        pass
    return json.dumps(text)


def _comment(line: str, comment: str) -> str:
    """Append a trailing ``# why``, aligned to a column where it still fits.

    The alignment used to be the whole rule, which glued the ``#`` straight onto
    any value 38 characters or longer — YAML then read the comment as part of
    the value, and the round-trip check caught it. Two spaces minimum.
    """
    padded = f"{line:<37}" if len(line) < 37 else line
    return f"{padded} # {comment}"


def _kv(key: str, value: Any, *, indent: int = 2, comment: str = "") -> str:
    pad = " " * indent
    line = f"{pad}{key}: {scalar(value)}"
    if comment:
        line = _comment(line, comment)
    return line


def _block(title: str, rows: list[str], *, header: str = "") -> list[str]:
    """One top-level section; nothing at all when it has no rows."""
    if not rows:
        return []
    out: list[str] = []
    if header:
        out.extend(f"# {line}" if line else "#" for line in header.splitlines())
    out.append(f"{title}:")
    out.extend(rows)
    out.append("")
    return out


def render(cfg: dict, *, header: str, comments: dict[str, str] | None = None) -> str:
    """Render ``cfg`` to YAML text.

    ``header`` is the file's leading comment block — the thing that tells the
    next reader what this profile is for. ``comments`` maps dotted paths to
    trailing explanations.
    """
    notes = comments or {}
    lines: list[str] = []

    for raw in header.strip().splitlines():
        lines.append(f"# {raw}".rstrip())
    lines.append("")

    for section in SECTION_ORDER:
        if section not in cfg:
            continue
        value = cfg[section]

        if not isinstance(value, dict):
            # `server: asgi` is the only scalar top-level key.
            comment = notes.get(section, "")
            line = f"{section}: {scalar(value)}"
            if comment:
                line = _comment(line, comment)
            lines.extend([line, ""])
            continue

        rows: list[str] = []
        for key, item in value.items():
            path = f"{section}.{key}"
            if isinstance(item, dict):
                rows.append(f"  {key}:")
                for sub_key, sub_item in item.items():
                    rows.append(_kv(
                        sub_key, sub_item, indent=4,
                        comment=notes.get(f"{path}.{sub_key}", ""),
                    ))
            elif isinstance(item, list):
                rows.append(f"  {key}:")
                rows.extend(f"    - {scalar(entry)}" for entry in item)
            else:
                rows.append(_kv(key, item, comment=notes.get(path, "")))
        lines.extend(_block(section, rows))

    # Anything the caller added that we do not have an opinion about, so an
    # unknown key is never silently dropped.
    for section, value in cfg.items():
        if section in SECTION_ORDER:
            continue
        rows = [_kv(k, v) for k, v in value.items()] if isinstance(value, dict) else []
        if rows:
            lines.extend(_block(section, rows))
        elif not isinstance(value, dict):
            lines.extend([f"{section}: {scalar(value)}", ""])

    return "\n".join(lines).rstrip() + "\n"


def verify_roundtrip(text: str, cfg: dict) -> None:
    """Parse our own output and assert it means what we meant.

    Cheap insurance against a quoting bug: the renderer hand-builds strings, so
    this is the only thing standing between a subtle escape error and a config
    that silently deploys the wrong thing.
    """
    parsed = yaml.safe_load(text)
    if parsed != cfg:
        raise ValueError(
            "rendered YAML does not round-trip to the config it came from; "
            f"expected {cfg!r}, parsed {parsed!r}"
        )
