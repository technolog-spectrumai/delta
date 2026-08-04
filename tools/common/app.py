"""The builder application shell, shared by every host.

Everything host-specific arrives as a ``HostSpec`` (see a builder's spec.py):
which fields to show, which presets exist, what to name the window, which icon
to wear. The window, the tabs, the validation fan-out and the save flow are the
same for all of them.

The signal model is enigma's: one ``changed`` on the model, one handler that
re-validates and calls ``refresh()`` + ``set_issues()`` on every tab. Tabs own
no state, so a preset click and a hand edit are indistinguishable downstream.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import deploy_bridge
import deployer
import theme
import validation
import widgets
import worker
import yaml_render
from config_model import ConfigModel

ORG_NAME = "toto"
SETTINGS_OUTPUT_KEY = "builder/output_dir"


# ---------------------------------------------------------------------------
# The host contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    """One editable config key."""

    path: str                       # dotted config path
    label: str
    kind: str = "text"              # text | int | choice | flag | bool | service
    choices: tuple[str, ...] = ()
    hint: str = ""
    comment: str = ""               # trailing # comment in the generated YAML
    omit_when: Any = ""             # value that means "leave the key out"
    locked: bool = False            # shown, not editable (e.g. aurelian's wsgi)


@dataclass(frozen=True)
class Capability:
    """One thing the platform can do, in the user's terms.

    The point of this layer is that nobody should have to know which BUILD_*
    flags imply channels, which need a celery worker, or that the graph wants a
    neo4j container. A capability names the outcome; ``derive_config`` works out
    the flags, the services and the server mode from the library's own feature
    closure.
    """

    key: str
    label: str
    blurb: str = ""
    flags: tuple[str, ...] = ()      # BUILD_*/INSTALL_* names this turns on
    services: tuple[str, ...] = ()   # infrastructure it needs directly
    env: tuple[tuple[str, str], ...] = ()   # extra env it cannot work without
    requires: tuple[str, ...] = ()   # capability keys this cannot work without


@dataclass(frozen=True)
class Preset:
    """A named starting point, modelled on a shipped profile."""

    key: str
    label: str
    blurb: str
    config: dict


@dataclass(frozen=True)
class HostSpec:
    host: str                       # zenobia | aurelian
    app_name: str                   # "Zenobia Builder"
    subtitle: str
    host_root: Path
    icon: Path
    header: str                     # leading comment block of the output file
    tabs: tuple[tuple[str, tuple[Field, ...]], ...]
    presets: tuple[Preset, ...]
    capabilities: tuple[Capability, ...] = ()
    extra_checks: Callable[[dict], list[validation.Issue]] | None = None
    default_filename: str = "custom.yaml"

    @property
    def configs_dir(self) -> Path:
        return self.host_root / self.host / "deploy" / "configs"

    @property
    def comments(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for _, fields in self.tabs:
            for f in fields:
                if f.comment:
                    out[f.path] = f.comment
        return out


# ---------------------------------------------------------------------------
# Turning chosen capabilities into a config
# ---------------------------------------------------------------------------

def is_selected(cap: Capability, cfg: dict) -> bool:
    env = cfg.get("env") or {}
    services = cfg.get("services") or {}
    if cap.flags and not all(str(env.get(f)) == "1" for f in cap.flags):
        return False
    if cap.services and not all(services.get(s) for s in cap.services):
        return False
    return bool(cap.flags or cap.services)


def get_locked_server(cfg: dict) -> str:
    """The server mode a host has fixed, if it has. delta is WSGI by build."""
    return str((cfg or {}).get("server") or "") if (cfg or {}).get("server") == "wsgi" else ""


def derive_config(capabilities: tuple[Capability, ...], host_root: Path,
                  cfg: dict) -> dict:
    """Fill in everything the user should not have to reason about.

    The server mode and the whole ``services:`` block are computed from the
    library's own feature closure, so an impossible combination cannot be
    expressed: channels-needing features get ASGI and a websocket proxy, the
    graph gets its container and its URI, git hosting gets Gitea.

    Infrastructure that no feature implies — the observability stack — stays a
    capability of its own, because nothing about the app decides it.
    """
    if not capabilities:
        return cfg

    env = dict(cfg.get("env") or {})
    by_key = {cap.key: cap for cap in capabilities}
    chosen = {cap.key for cap in capabilities if is_selected(cap, cfg)}

    # Pull in what the chosen ones cannot work without, transitively. Weather
    # without geometry is not a preference, it is a FeatureConfigError — so
    # ticking weather turns maps back on rather than letting you build a config
    # the image will refuse.
    pending = list(chosen)
    while pending:
        cap = by_key.get(pending.pop())
        if cap is None:
            continue
        for key in cap.requires:
            if key not in chosen:
                chosen.add(key)
                pending.append(key)
    for key in chosen:
        cap = by_key.get(key)
        if cap is None:
            continue
        for flag in cap.flags:
            env[flag] = "1"

    # Capabilities whose services are theirs alone, captured before the block
    # below is rebuilt from scratch.
    explicit: dict[str, bool] = {}
    for cap in capabilities:
        if cap.key not in chosen:
            continue
        for service in cap.services:
            explicit[service] = True
        for key, value in cap.env:
            env.setdefault(key, value)

    features, _ = deploy_bridge.feature_closure(host_root, env)
    # Start from the services the config already declares, NOT from an empty
    # dict as the monorepo copy does. delta's celery worker and beat are part of
    # the host rather than something a capability turns on (the recommendation
    # matrix is recomputed nightly), and rebuilding this block from the feature
    # closure alone would silently delete them from every generated config.
    services = {k: v for k, v in (cfg.get("services") or {}).items() if v}
    services.update(explicit)

    if features.get("needs_channels"):
        # Anything that talks over a websocket needs the nginx upgrade headers
        # and an ASGI server (below).
        services["websockets"] = True
    if features.get("workflows"):
        # Workflows need a task queue. Without a worker they fall back to
        # running inline, which works but blocks the request. (No kernel
        # server: the Python editor was retired — see zenobia/limbo/README.md.)
        services["celery"] = True
    if features.get("gitvault"):
        services["gitea"] = True

    out = dict(cfg)
    out["env"] = env
    # A host that pins its server mode (delta ships no asgi.py) keeps it; only
    # a host that leaves the decision open has it derived.
    if not get_locked_server(cfg):
        out["server"] = "asgi" if features.get("needs_channels") else "wsgi"
    if services:
        out["services"] = dict(sorted(services.items()))
    else:
        out.pop("services", None)
    return out


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

class FieldTab(QWidget):
    """A card of fields bound to the model by dotted path."""

    def __init__(self, spec: HostSpec, model: ConfigModel, fields: tuple[Field, ...]) -> None:
        super().__init__()
        self.spec = spec
        self.model = model
        self.fields = fields
        self._editors: dict[str, QWidget] = {}
        self._syncing = False

        outer = widgets.page_layout(self)
        page = widgets.scroll_page(outer)

        frame, form = widgets.form_card()
        for f in fields:
            editor = self._build_editor(f)
            self._editors[f.path] = editor
            form.addRow(f.label, editor)
            if f.hint:
                form.addRow("", widgets.hint(f.hint, "gateHint"))
        page.addWidget(frame)

        self.errors = widgets.ErrorLabel()
        page.addWidget(self.errors)
        page.addStretch(1)

    def _build_editor(self, f: Field) -> QWidget:
        if f.kind == "int":
            box = widgets.spin(0, 65535)
            box.valueChanged.connect(lambda v, p=f.path: self._write(p, int(v)))
            box.setEnabled(not f.locked)
            return box
        if f.kind == "choice":
            combo = QComboBox()
            combo.addItems(list(f.choices))
            combo.currentTextChanged.connect(lambda v, p=f.path: self._write(p, v))
            combo.setEnabled(not f.locked)
            return combo
        if f.kind in ("flag", "bool", "service"):
            box = QCheckBox()
            box.toggled.connect(lambda v, ff=f: self._write_toggle(ff, v))
            box.setEnabled(not f.locked)
            return box
        edit = QLineEdit()
        edit.setClearButtonEnabled(True)
        edit.textChanged.connect(lambda v, ff=f: self._write_text(ff, v))
        edit.setEnabled(not f.locked)
        return edit

    def _write(self, path: str, value: Any) -> None:
        if not self._syncing:
            self.model.set(path, value)

    def _write_text(self, f: Field, value: str) -> None:
        if not self._syncing:
            self.model.set_or_unset(f.path, value.strip(), omit_when=f.omit_when)

    def _write_toggle(self, f: Field, on: bool) -> None:
        if self._syncing:
            return
        if f.kind in ("service", "bool"):
            # Real YAML booleans, and an unticked one must vanish rather than
            # appear as `false`. Note a "flag"-style "0" would be WRONG here:
            # deploy.py reads these with plain truthiness, and the string "0"
            # is truthy — ssl.staging: "0" would silently enable staging.
            if on:
                self.model.set(f.path, True)
            else:
                self.model.unset(f.path)
        else:
            # BUILD_* flags are quoted strings; "1" is the only true value.
            self.model.set(f.path, "1" if on else "0")

    def refresh(self) -> None:
        self._syncing = True
        try:
            for f in self.fields:
                editor = self._editors[f.path]
                value = self.model.get(f.path)
                if isinstance(editor, QComboBox):
                    editor.setCurrentText(str(value or (f.choices[0] if f.choices else "")))
                elif isinstance(editor, QCheckBox):
                    editor.setChecked(value is True or str(value) == "1")
                elif isinstance(editor, QLineEdit):
                    editor.setText("" if value is None else str(value))
                else:
                    editor.setValue(int(value or 0))
        finally:
            self._syncing = False

    def set_issues(self, issues: list[validation.Issue]) -> None:
        owned = validation.issues_for(issues, *(f.path for f in self.fields))
        self.errors.set_messages([f"{i.path}: {i.message}" for i in owned])

    def has_errors(self, issues: list[validation.Issue]) -> bool:
        owned = validation.issues_for(issues, *(f.path for f in self.fields))
        return any(i.is_error for i in owned)


class CapabilityTab(QWidget):
    """One list of things the platform can do. Tick what you want.

    No compatibility decisions live here: the server mode, the websocket
    proxy, the task worker, the compute kernel, the graph container and the git
    host are all worked out from the selection.
    """

    def __init__(self, spec: HostSpec, model: ConfigModel, window: "BuilderWindow") -> None:
        super().__init__()
        self.spec = spec
        self.model = model
        self.window = window
        self._boxes: dict[str, QCheckBox] = {}
        self._syncing = False

        outer = widgets.page_layout(self)
        page = widgets.scroll_page(outer)

        frame, layout = widgets.card()
        layout.addWidget(widgets.section_title("Features"))
        layout.addWidget(widgets.hint(
            "Pick what this platform should do. Everything else — the server "
            "mode, the background worker, the websocket proxy, the graph "
            "database, the git host — is worked out from your choice."
        ))
        for cap in spec.capabilities:
            box = QCheckBox(cap.label)
            box.setToolTip(cap.blurb)
            box.toggled.connect(lambda on, c=cap: self._toggle(c, on))
            self._boxes[cap.key] = box
            layout.addWidget(box)
            if cap.blurb:
                layout.addWidget(widgets.hint(cap.blurb, "gateHint"))
        page.addWidget(frame)

        derived_frame, derived_layout = widgets.card()
        derived_layout.addWidget(widgets.section_title("Which gives you"))
        self.derived = widgets.hint("")
        derived_layout.addWidget(self.derived)
        page.addWidget(derived_frame)
        page.addStretch(1)

    def _toggle(self, cap: Capability, on: bool) -> None:
        if self._syncing:
            return
        with self.model.batch():
            for flag in cap.flags:
                self.model.set(f"env.{flag}", "1" if on else "0")
            for service in cap.services:
                if on:
                    self.model.set(f"services.{service}", True)
                else:
                    self.model.unset(f"services.{service}")
            if not on:
                for key, _ in cap.env:
                    self.model.unset(f"env.{key}")
            self.model.replace(derive_config(
                self.spec.capabilities, self.spec.host_root, self.model.data))

    def refresh(self) -> None:
        cfg = self.model.data
        self._syncing = True
        try:
            for cap in self.spec.capabilities:
                self._boxes[cap.key].setChecked(is_selected(cap, cfg))
        finally:
            self._syncing = False

        services = sorted((cfg.get("services") or {}))
        server = cfg.get("server", "wsgi")
        lines = [f"• {server} server "
                 + ("(uvicorn — some features need websockets)" if server == "asgi"
                    else "(gunicorn — nothing here needs websockets)")]
        if services:
            lines.append("• containers: " + ", ".join(services))
        else:
            lines.append("• containers: web, postgres, redis and nginx only")
        self.derived.setText("\n".join(lines))

    def set_issues(self, issues: list[validation.Issue]) -> None:
        return

    def has_errors(self, issues: list[validation.Issue]) -> bool:
        return False


class OutputTab(QWidget):
    """Preset picker, YAML preview, and the Save action."""

    def __init__(self, spec: HostSpec, model: ConfigModel, window: "BuilderWindow") -> None:
        super().__init__()
        self.spec = spec
        self.model = model
        self.window = window

        outer = widgets.page_layout(self)
        page = widgets.scroll_page(outer)

        frame, layout = widgets.card()
        layout.addWidget(widgets.section_title("Start from a profile"))
        layout.addWidget(widgets.hint(
            "Presets mirror the profiles already shipped with this host. Pick "
            "the closest one, then adjust on the other tabs."
        ))
        chips = QHBoxLayout()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for preset in spec.presets:
            chip = QPushButton(preset.label)
            chip.setObjectName("presetChip")
            chip.setCheckable(True)
            chip.setToolTip(preset.blurb)
            chip.clicked.connect(lambda _=False, p=preset: self._apply(p))
            self._group.addButton(chip)
            chips.addWidget(chip)
        chips.addStretch()
        layout.addLayout(chips)
        page.addWidget(frame)

        name_frame, name_form = widgets.form_card()
        self.filename = QLineEdit(spec.default_filename)
        name_form.addRow("File name", self.filename)
        self.folder = widgets.PathRow(
            "Choose an output folder",
            on_default=lambda: str(spec.configs_dir),
        )
        self.folder.set_value(str(spec.configs_dir))
        name_form.addRow("Output folder", self.folder)
        page.addWidget(name_frame)

        preview_frame, preview_layout = widgets.card()
        preview_layout.addWidget(widgets.section_title("Preview"))
        self.preview = widgets.MonoPane("preview", "The generated YAML appears here.", 4000)
        preview_layout.addWidget(self.preview)
        page.addWidget(preview_frame, 1)

        self.errors = widgets.ErrorLabel()
        outer.addWidget(self.errors)

        actions = QHBoxLayout()
        actions.addStretch()
        self.save_button = QPushButton("Save config")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        outer.addLayout(actions)

    def _apply(self, preset: Preset) -> None:
        self.model.replace(copy.deepcopy(preset.config))

    def refresh(self) -> None:
        try:
            text = self.window.render()
        except Exception as exc:  # a render bug must not kill the window
            text = f"# cannot render: {exc}"
        self.preview.set_content(text)

    def set_issues(self, issues: list[validation.Issue]) -> None:
        blocking = validation.errors(issues)
        notes = validation.warnings(issues)
        lines = [f"{i.path}: {i.message}" if i.path else i.message for i in blocking[:5]]
        if len(blocking) > 5:
            lines.append(f"…and {len(blocking) - 5} more")
        lines += [f"warning — {i.path}: {i.message}" if i.path else f"warning — {i.message}"
                  for i in notes[:5]]
        self.errors.set_messages(lines)
        self.save_button.setEnabled(not blocking)

    def write_config(self, target: Path, parent: QWidget,
                     *, skip_prompt_when_identical: bool = False) -> Path | None:
        """Confirm an overwrite, render, write. None when the user declined.

        Shared with the Deploy tab so there is one overwrite semantic rather
        than two that drift. ``skip_prompt_when_identical`` lets Deploy re-save
        the same bytes without asking again on every Stop or Logs.
        """
        try:
            text = self.window.render()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(parent, self.spec.app_name,
                                 f"Cannot render the config:\n{exc}")
            return None

        if target.exists():
            unchanged = False
            if skip_prompt_when_identical:
                try:
                    unchanged = target.read_text(encoding="utf-8") == text
                except OSError:
                    unchanged = False
            if not unchanged:
                answer = QMessageBox.question(
                    parent, self.spec.app_name,
                    f"{target} already exists.\n\nOverwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return None

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(parent, self.spec.app_name,
                                 f"Cannot write the config:\n{exc}")
            return None
        return target

    def _save(self) -> None:
        folder = self.folder.path()
        if folder is None:
            QMessageBox.critical(self, self.spec.app_name, "Choose an output folder first.")
            return
        name = deployer.normalise_filename(self.filename.text(), self.spec.default_filename)
        target = folder / name

        if self.write_config(target, self) is None:
            return

        QSettings().setValue(SETTINGS_OUTPUT_KEY, str(folder))
        hint = ""
        if folder.resolve() != self.spec.configs_dir.resolve():
            hint = (
                "\n\nNote: deploy.py only accepts a config under "
                f"{self.spec.configs_dir} unless you set HOST_ROOT yourself."
            )
        QMessageBox.information(
            self, self.spec.app_name, f"Wrote {target}{hint}"
        )


class ManualTab(QWidget):
    """Edit the config as YAML, for anything the forms do not cover.

    The forms are a curated subset; this is the escape hatch. Applying parses
    the text and replaces the whole config, so the other tabs immediately
    reflect whatever was typed — including keys no form knows about, which the
    renderer passes through untouched.

    The text is only re-synced from the model while it is clean. Once edited it
    is left alone, because silently overwriting what someone is typing is worse
    than showing them a stale view they can refresh deliberately.
    """

    def __init__(self, spec: HostSpec, model: ConfigModel, window: "BuilderWindow") -> None:
        super().__init__()
        self.spec = spec
        self.model = model
        self.window = window
        self._dirty = False
        self._syncing = False

        outer = widgets.page_layout(self)

        header, header_layout = widgets.card()
        header_layout.addWidget(widgets.section_title("Edit the config directly"))
        header_layout.addWidget(widgets.hint(
            "Everything the forms produce, plus anything they do not offer. "
            "Apply parses it and updates the other tabs; Revert throws your "
            "edits away and re-reads the form."
        ))
        outer.addWidget(header)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("preview")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.textChanged.connect(self._on_edited)
        outer.addWidget(self.editor, 1)

        self.errors = widgets.ErrorLabel()
        outer.addWidget(self.errors)

        actions = QHBoxLayout()
        self.state = QLabel("")
        self.state.setObjectName("details")
        actions.addWidget(self.state)
        actions.addStretch()
        self.revert_button = QPushButton("Revert")
        self.revert_button.clicked.connect(self._revert)
        actions.addWidget(self.revert_button)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self._apply)
        actions.addWidget(self.apply_button)
        outer.addLayout(actions)

        self._sync()

    def _on_edited(self) -> None:
        if self._syncing:
            return
        self._dirty = True
        self.state.setText("edited — Apply to use it, Revert to discard")

    def _sync(self) -> None:
        """Pull the current config into the editor."""
        self._syncing = True
        try:
            self.editor.setPlainText(self.window.render())
        except Exception as exc:  # a render bug must not empty the editor
            self.editor.setPlainText(f"# cannot render: {exc}")
        finally:
            self._syncing = False
        self._dirty = False
        self.state.setText("")
        self.errors.set_messages([])

    def _apply(self) -> None:
        import yaml

        text = self.editor.toPlainText()
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self.errors.set_messages([f"YAML error: {exc}"])
            return
        if not isinstance(parsed, dict):
            self.errors.set_messages(["The config must be a YAML mapping."])
            return

        self.errors.set_messages([])
        self._dirty = False
        self.state.setText("applied")
        # Replacing emits `changed`, so every other tab re-reads and the whole
        # validation ladder runs against what was typed.
        self.model.replace(parsed)

    def _revert(self) -> None:
        self._sync()

    def refresh(self) -> None:
        if not self._dirty:
            self._sync()

    def set_issues(self, issues: list[validation.Issue]) -> None:
        if self._dirty:
            return
        blocking = validation.errors(issues)
        self.errors.set_messages(
            [f"{i.path}: {i.message}" if i.path else i.message for i in blocking[:6]]
        )

    def has_errors(self, issues: list[validation.Issue]) -> bool:
        return False


class _DeployTabBase(QWidget):
    """Shared chrome and child-process plumbing for the Local and Remote tabs.

    Both tabs are the same machine underneath: the facts card, the log pane,
    the cancel/status row, one ProcessJob at a time, and the per-stack flock.
    What differs — which commands exist, which guards apply, what gets a
    confirmation — lives in the subclasses. The lock PATH is shared too, so a
    local up and a push of the same stack refuse to overlap (both stage wheels
    into the shared ``<host>/dist``).
    """

    def __init__(self, spec: HostSpec, model: ConfigModel, window: "BuilderWindow") -> None:
        super().__init__()
        self.spec = spec
        self.model = model
        self.window = window
        self.job: worker.ProcessJob | None = None
        self.lock: deployer.DeployLock | None = None
        self._issues: list[validation.Issue] = []
        self._command = ""
        self._dry_run = False

        outer = widgets.page_layout(self)
        page = widgets.scroll_page(outer)

        self._build_intro(page)

        facts_frame, facts_form = widgets.form_card()
        self.config_label = QLabel("")
        self.config_label.setObjectName("details")
        self.config_label.setWordWrap(True)
        facts_form.addRow("Config file", self.config_label)
        self.stack_label = QLabel("")
        self.stack_label.setObjectName("details")
        facts_form.addRow("Stack", self.stack_label)
        self._build_facts(facts_form)
        self.command_label = QLabel("")
        self.command_label.setObjectName("details")
        self.command_label.setWordWrap(True)
        self.command_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        facts_form.addRow("Command", self.command_label)
        page.addWidget(facts_frame)

        self._build_options(page)

        log_frame, log_layout = widgets.card()
        log_layout.addWidget(widgets.section_title("Deploy log"))
        # 4000, not the packager's 2000: maximumBlockCount drops the oldest
        # lines silently and a cold image build runs well past 2000.
        self.log = widgets.MonoPane("log", "The deploy log appears here.", 4000)
        log_layout.addWidget(self.log)
        page.addWidget(log_frame, 1)

        self.errors = widgets.ErrorLabel()
        outer.addWidget(self.errors)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        outer.addWidget(self.progress)

        actions = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("details")
        actions.addWidget(self.status)
        actions.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_button)
        self._build_buttons(actions)
        outer.addLayout(actions)

    # -- subclass hooks ----------------------------------------------------
    def _build_intro(self, page) -> None:
        raise NotImplementedError

    def _build_facts(self, facts_form) -> None:
        """Extra facts rows between Stack and Command (the Remote tab's Server)."""

    def _build_options(self, page) -> None:
        raise NotImplementedError

    def _build_buttons(self, actions) -> None:
        raise NotImplementedError

    @property
    def _buttons(self) -> tuple[QPushButton, ...]:
        raise NotImplementedError

    @property
    def _boxes(self) -> tuple[QCheckBox, ...]:
        return ()

    # -- shared plumbing ---------------------------------------------------
    def _target_path(self) -> Path:
        return deployer.config_target(
            self.spec, self.window.output_tab.filename.text()
        )

    def _write_config(self) -> Path | None:
        # EVERY command saves first — Stop and Logs too: deploy.py regenerates
        # the compose file from whatever config it is handed, so a stale file
        # would run against a different project than the one on screen.
        return self.window.output_tab.write_config(
            self._target_path(), self, skip_prompt_when_identical=True
        )

    def _launch(self, cfg: dict, argv: list[str], command: str,
                *, dry_run: bool = False) -> None:
        lock = deployer.DeployLock(deployer.lock_path(self.spec, cfg))
        if not lock.acquire():
            QMessageBox.critical(
                self, self.spec.app_name,
                f"A deploy for project '{deployer.stack_name(self.spec, cfg)}' is "
                "already running — in another window, on the other deploy tab, or "
                "from deploy/run.sh.\n\n"
                "Overlapping builds deadlock each other, so this one will not start.",
            )
            return
        self.lock = lock

        self._command = command
        # Recorded at launch: _on_exit must not re-read a checkbox to decide
        # what just happened, even though the boxes are disabled while running.
        self._dry_run = dry_run
        self.log.set_content("")
        self.log.append_line("$ " + deployer.describe(argv))
        self._set_running(True)

        self.job = worker.ProcessJob(
            argv,
            cwd=self.spec.host_root,
            env=deployer.child_env(self.spec),
            clean=deployer.clean_line,
        )
        self.job.log_message.connect(self.log.append_line)
        self.job.job_finished.connect(self._on_exit)
        self.job.job_failed.connect(self._on_launch_failed)
        self.job.job_cancelled.connect(self._on_cancelled)
        self.job.finished.connect(lambda: self._set_running(False))
        self.job.start()

    def _cancel(self) -> None:
        if self.job is not None:
            self.status.setText("Stopping…")
            self.job.request_cancel()

    def _set_running(self, running: bool) -> None:
        for button in self._buttons:
            button.setEnabled(not running)
        for box in self._boxes:
            box.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setText(
            "Stop following" if running and self._command == "logs" else "Cancel"
        )
        # An indeterminate bar: a deploy has nothing countable, and a percentage
        # would be a fiction.
        if running:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
        if not running:
            self.job = None
            if self.lock is not None:
                self.lock.release()
                self.lock = None
            # Re-apply rather than blindly re-enable: the config may have gone
            # invalid while the deploy ran.
            self.set_issues(self._issues)
            self.refresh()

    def _on_exit(self, code: int) -> None:
        if code == 0:
            self.progress.setValue(100)
            done = {"up": "Deployed", "down": "Stopped", "logs": "Log stream ended",
                    "push": "Dry run finished" if self._dry_run else "Pushed",
                    "provision": "Provisioned"}
            self.status.setText(done.get(self._command, "Done"))
            if self._command == "up":
                QMessageBox.information(
                    self, self.spec.app_name,
                    "The stack is up. The log lists the URLs it is serving on.",
                )
            elif self._command == "push" and not self._dry_run:
                QMessageBox.information(
                    self, self.spec.app_name,
                    "Pushed — the server rebuilt and started the stack. The log "
                    "above shows the remote compose output.",
                )
            return
        self.progress.setValue(0)
        self.status.setText("Failed")
        self.log.append_line(f"# FAILED: deploy.py exited {code}")
        QMessageBox.critical(
            self, self.spec.app_name,
            f"deploy.py exited with code {code}. The log above says why.",
        )

    def _on_launch_failed(self, message: str) -> None:
        self.progress.setValue(0)
        self.status.setText("Failed")
        self.log.append_line(f"# FAILED to start: {message}")
        QMessageBox.critical(self, self.spec.app_name,
                             f"Could not start deploy.py:\n{message}")

    def _on_cancelled(self) -> None:
        self.progress.setValue(0)
        self.status.setText("Cancelled")
        if self._command == "up":
            # There is no partial artifact to delete here, unlike the packager —
            # there is a partially started stack, and saying otherwise is a trap.
            self.log.append_line(
                "# cancelled — the image build was abandoned, but any containers "
                "that already started are still running. Use Stop to remove them."
            )
        elif self._command == "push" and not self._dry_run:
            # rsync --delete deletes DURING transfer, so a cancel mid-copy leaves
            # the server's tree part-old/part-new; a cancel during the remote
            # rebuild drops the ssh session while the server is mid-build.
            self.log.append_line(
                "# cancelled — the server may be left part-synced or mid-rebuild. "
                "Push again to bring it back to a consistent state."
            )
        elif self._command == "provision":
            self.log.append_line(
                "# cancelled — provisioning was interrupted partway. Its steps "
                "are safe to repeat: run Provision again to finish."
            )
        else:
            self.log.append_line("# cancelled")


class DeployTab(_DeployTabBase):
    """Run this config as a stack on this machine — the Local tab.

    deploy_local.sh with a window around it: deploy.py up/down/logs against the
    saved config. Refuses a config aimed at a remote host — that is the Remote
    tab's job — so a production profile can never be stood up on a laptop by
    mis-click.
    """

    def _build_intro(self, page) -> None:
        frame, layout = widgets.card()
        layout.addWidget(widgets.section_title("Run this stack on this machine"))
        layout.addWidget(widgets.hint(
            "Saves the config into the host's deploy/configs folder and then runs "
            f"{self.spec.host}'s own deploy.py against it — the same thing "
            "deploy_local.sh does. Deploy starts the stack in the background, "
            "Stop takes it down (volumes survive), and Logs follows it until you "
            "stop watching."
        ))
        layout.addWidget(widgets.hint(
            "Needs docker and the compose plugin on this machine, and a config "
            "whose target is local — a server profile belongs on the Remote tab. "
            "The first build is slow — several minutes — and everything the tool "
            "prints appears in the log below.",
            "gateHint",
        ))
        page.addWidget(frame)

    def _build_options(self, page) -> None:
        opts_frame, opts_layout = widgets.card()
        opts_layout.addWidget(widgets.section_title("Build options"))
        self.rebuild_box = QCheckBox("Force a full rebuild")
        self.rebuild_box.toggled.connect(self.refresh)
        opts_layout.addWidget(self.rebuild_box)
        opts_layout.addWidget(widgets.hint(
            "Drops --smart, which otherwise reuses the image when nothing baked "
            "into it changed. Same as BUILD=1 ./deploy_local.sh.", "gateHint"))
        self.dev_box = QCheckBox("Unpinned build from the vendored tree (--dev)")
        self.dev_box.toggled.connect(self.refresh)
        opts_layout.addWidget(self.dev_box)
        opts_layout.addWidget(widgets.hint(
            "Builds against the toto library as it is right now instead of the "
            "pinned tag, and skips the check that refuses a build when "
            "vendor/toto_libs has uncommitted changes.", "gateHint"))
        self.reset_box = QCheckBox("Wipe and re-seed the database (--reset-db)")
        self.reset_box.toggled.connect(self.refresh)
        opts_layout.addWidget(self.reset_box)
        self.fake_box = QCheckBox("Seed demo data (--fake-data)")
        self.fake_box.toggled.connect(self.refresh)
        opts_layout.addWidget(self.fake_box)
        self.destructive = widgets.hint("", "gateHint")
        opts_layout.addWidget(self.destructive)
        page.addWidget(opts_frame)

    def _build_buttons(self, actions) -> None:
        self.logs_button = QPushButton("Logs")
        self.logs_button.clicked.connect(lambda: self._run("logs"))
        actions.addWidget(self.logs_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(lambda: self._run("down"))
        actions.addWidget(self.stop_button)
        self.deploy_button = QPushButton("Deploy")
        self.deploy_button.setObjectName("primaryButton")
        self.deploy_button.clicked.connect(lambda: self._run("up"))
        actions.addWidget(self.deploy_button)

    @property
    def _buttons(self) -> tuple[QPushButton, ...]:
        return (self.deploy_button, self.stop_button, self.logs_button)

    @property
    def _boxes(self) -> tuple[QCheckBox, ...]:
        return (self.rebuild_box, self.dev_box, self.reset_box, self.fake_box)

    def _switches(self, command: str) -> dict[str, bool]:
        return deployer.filter_switches(
            command,
            force_rebuild=self.rebuild_box.isChecked(),
            dev=self.dev_box.isChecked(),
            reset_db=self.reset_box.isChecked(),
            fake_data=self.fake_box.isChecked(),
        )

    def refresh(self) -> None:
        if self.job is not None:
            return
        cfg = self.model.data
        target = self._target_path()
        self.config_label.setText(str(target))
        self.stack_label.setText(deployer.stack_name(self.spec, cfg))
        try:
            argv = deployer.build_argv(self.spec, target, "up", **self._switches("up"))
            self.command_label.setText(deployer.describe(argv))
        except (ValueError, deployer.DeployError) as exc:
            self.command_label.setText(str(exc))
        notes = deployer.destructive_notes(cfg, reset_db=self.reset_box.isChecked())
        self.destructive.setText(" ".join(notes))
        self.destructive.setVisible(bool(notes))

    def set_issues(self, issues: list[validation.Issue]) -> None:
        self._issues = issues
        blocking = validation.errors(issues)
        messages: list[str] = []
        if blocking:
            messages = [
                "Fix the errors on the other tabs before deploying:",
                *[f"{i.path}: {i.message}" if i.path else i.message
                  for i in blocking[:4]],
            ]
        remote = deployer.local_target_issue(self.model.data)
        if remote:
            messages.append(remote)
        self.errors.set_messages(messages)
        if self.job is None:
            for button in self._buttons:
                button.setEnabled(not blocking and not remote)

    def _run(self, command: str) -> None:
        if self.job is not None:
            return
        cfg = self.model.data

        issue = deployer.local_target_issue(cfg)
        if issue:
            QMessageBox.critical(self, self.spec.app_name, issue)
            return

        reasons = deployer.blockers(self.spec, cfg)
        if reasons:
            self.log.append_line("\n".join(f"# {r}" for r in reasons))
            QMessageBox.critical(
                self, self.spec.app_name,
                "This machine cannot run the deploy yet:\n\n"
                + "\n\n".join(reasons),
            )
            return

        switches = self._switches(command)
        if command == "up":
            notes = deployer.destructive_notes(cfg, reset_db=switches["reset_db"])
            if notes:
                answer = QMessageBox.warning(
                    self, self.spec.app_name,
                    f"Deploying '{deployer.stack_name(self.spec, cfg)}' will destroy "
                    "data on this machine:\n\n" + "\n\n".join(notes) + "\n\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

        target = self._write_config()
        if target is None:
            return

        try:
            argv = deployer.build_argv(self.spec, target, command, **switches)
        except (ValueError, deployer.DeployError) as exc:
            QMessageBox.critical(self, self.spec.app_name, str(exc))
            return

        self._launch(cfg, argv, command)


class RemoteTab(_DeployTabBase):
    """Deploy this config to its server over SSH — the Remote tab.

    deploy_server.sh with a window around it: Push rsyncs the whole repo to the
    server and rebuilds the stack there; Provision does the first-time server
    setup (docker, tailscale, certificates). Both require a remote target —
    the mirror image of the Local tab's refusal — and Push always confirms
    before touching the server unless it is a dry run.
    """

    def _build_intro(self, page) -> None:
        frame, layout = widgets.card()
        layout.addWidget(widgets.section_title("Deploy to the server over SSH"))
        layout.addWidget(widgets.hint(
            "Saves the config and then runs "
            f"{self.spec.host}'s own deploy.py against it — the same thing "
            "deploy_server.sh does. Push rsyncs the repo to the server and "
            "rebuilds the stack there; Provision does the first-time setup "
            "(docker, and — if configured — tailscale and certificates). The "
            "server comes from the target fields on the Network tab."
        ))
        layout.addWidget(widgets.hint(
            "Needs ssh and rsync on this machine and key-based SSH access to "
            "the server: SSH runs with BatchMode, so it fails rather than asks "
            "for a password. Docker on the server comes from Provision. "
            "Everything the tool prints — rsync and the remote build included — "
            "appears in the log below.",
            "gateHint",
        ))
        page.addWidget(frame)

    def _build_facts(self, facts_form) -> None:
        self.server_label = QLabel("")
        self.server_label.setObjectName("details")
        self.server_label.setWordWrap(True)
        facts_form.addRow("Server", self.server_label)

    def _build_options(self, page) -> None:
        # No push options at all in delta's copy. The upstream builder offers a
        # "--dry-run" preview; delta's deploy.py fork does not accept the flag,
        # and a checkbox that says "preview only" while performing a real push
        # is worse than no checkbox.
        opts_frame, opts_layout = widgets.card()
        opts_layout.addWidget(widgets.section_title("Push options"))
        opts_layout.addWidget(widgets.hint(
            "None. A push always rsyncs the payload and rebuilds on the server "
            "from what was synced — there are no build switches, and this host's "
            "deploy tool has no preview mode.",
            "gateHint"))
        page.addWidget(opts_frame)

    def _build_buttons(self, actions) -> None:
        self.provision_button = QPushButton("Provision")
        self.provision_button.clicked.connect(lambda: self._run("provision"))
        actions.addWidget(self.provision_button)
        self.push_button = QPushButton("Push")
        self.push_button.setObjectName("primaryButton")
        self.push_button.clicked.connect(lambda: self._run("push"))
        actions.addWidget(self.push_button)

    @property
    def _buttons(self) -> tuple[QPushButton, ...]:
        return (self.push_button, self.provision_button)

    @property
    def _boxes(self) -> tuple[QCheckBox, ...]:
        return ()

    def refresh(self) -> None:
        if self.job is not None:
            return
        cfg = self.model.data
        target = self._target_path()
        self.config_label.setText(str(target))
        self.stack_label.setText(deployer.stack_name(self.spec, cfg))
        self.server_label.setText(deployer.remote_summary(cfg))
        try:
            argv = deployer.build_remote_argv(
                self.spec, target, "push")
            self.command_label.setText(deployer.describe(argv))
        except (ValueError, deployer.DeployError) as exc:
            self.command_label.setText(str(exc))

    def set_issues(self, issues: list[validation.Issue]) -> None:
        self._issues = issues
        blocking = validation.errors(issues)
        messages: list[str] = []
        if blocking:
            messages = [
                "Fix the errors on the other tabs before deploying:",
                *[f"{i.path}: {i.message}" if i.path else i.message
                  for i in blocking[:4]],
            ]
        remote_issue = deployer.remote_target_issue(self.model.data)
        if remote_issue:
            messages.append(remote_issue)
        self.errors.set_messages(messages)
        if self.job is None:
            for button in self._buttons:
                button.setEnabled(not blocking and not remote_issue)

    def _run(self, command: str) -> None:
        if self.job is not None:
            return
        cfg = self.model.data
        dry_run = False

        issue = deployer.remote_target_issue(cfg)
        if issue:
            QMessageBox.critical(self, self.spec.app_name, issue)
            return

        reasons = deployer.remote_blockers(self.spec, cfg, command)
        if reasons:
            self.log.append_line("\n".join(f"# {r}" for r in reasons))
            QMessageBox.critical(
                self, self.spec.app_name,
                "This machine cannot run the deploy yet:\n\n"
                + "\n\n".join(reasons),
            )
            return

        if not dry_run:
            # A push replaces whatever stack the server is running; provision
            # installs software on it. Neither should ever be a mis-click.
            where = deployer.remote_summary(cfg)
            what = (
                f"Push the repo to {where} and rebuild the stack there?\n\n"
                "The server's running stack is replaced (its volumes survive)."
                if command == "push"
                else f"Provision {where}?\n\nThis installs docker (and, if "
                "configured, tailscale and certificates) on the server."
            )
            answer = QMessageBox.warning(
                self, self.spec.app_name, what,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        target = self._write_config()
        if target is None:
            return

        try:
            argv = deployer.build_remote_argv(
                self.spec, target, command, dry_run=dry_run)
        except (ValueError, deployer.DeployError) as exc:
            QMessageBox.critical(self, self.spec.app_name, str(exc))
            return

        self._launch(cfg, argv, command, dry_run=dry_run)


class BuilderWindow(QMainWindow):
    def __init__(self, spec: HostSpec) -> None:
        super().__init__()
        self.spec = spec
        self.settings = QSettings()
        self.model = ConfigModel(spec.presets[0].config if spec.presets else {})

        self.setWindowTitle(spec.app_name)
        if spec.icon.is_file():
            self.setWindowIcon(QIcon(str(spec.icon)))
        self.setMinimumSize(820, 680)
        self.resize(960, 880)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel(spec.app_name)
        title.setObjectName("title")
        title.setFont(QFont(title.font().family(), 21, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("secondaryButton")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        root.addLayout(header)

        subtitle = QLabel(spec.subtitle)
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.field_tabs: list[FieldTab] = []
        for label, fields in spec.tabs:
            tab = FieldTab(spec, self.model, fields)
            self.field_tabs.append(tab)
            self.tabs.addTab(tab, label)
        self.capability_tab = None
        if spec.capabilities:
            self.capability_tab = CapabilityTab(spec, self.model, self)
            self.tabs.addTab(self.capability_tab, "Features")

        self.output_tab = OutputTab(spec, self.model, self)
        self.tabs.addTab(self.output_tab, "Output")
        self.manual_tab = ManualTab(spec, self.model, self)
        self.tabs.addTab(self.manual_tab, "Edit YAML")
        # Local and Remote sit before Package: run it here, push it to its
        # server, or ship it elsewhere. Both must be constructed after
        # output_tab, whose filename field they read.
        self.deploy_tab = DeployTab(spec, self.model, self)
        self.tabs.addTab(self.deploy_tab, "Local")
        self.remote_tab = RemoteTab(spec, self.model, self)
        self.tabs.addTab(self.remote_tab, "Remote")
        root.addWidget(self.tabs, 1)

        self._theme = str(self.settings.value(theme.SETTINGS_THEME_KEY, "") or theme.detect_system_theme())
        self._apply_theme()

        self.model.changed.connect(self._on_changed)
        self._on_changed()

    # -- config -----------------------------------------------------------
    def config(self) -> dict:
        return self.model.data

    def render(self) -> str:
        cfg = self.config()
        text = yaml_render.render(cfg, header=self.spec.header, comments=self.spec.comments)
        yaml_render.verify_roundtrip(text, cfg)
        return text

    def validate(self) -> list[validation.Issue]:
        cfg = self.config()
        issues = validation.check_common(cfg)
        issues += validation.check_flags(cfg.get("env") or {})
        if self.spec.extra_checks:
            issues += self.spec.extra_checks(cfg)
        try:
            issues += deploy_bridge.validate(
                self.spec.host_root, cfg, self.spec.configs_dir / "generated.yaml"
            )
            _, closure_issues = deploy_bridge.feature_closure(
                self.spec.host_root, cfg.get("env") or {}
            )
            issues += closure_issues
        except Exception as exc:
            issues.append(validation.Issue("", f"deploy.py could not be consulted: {exc}",
                                           validation.WARNING))
        return issues

    def _on_changed(self) -> None:
        issues = self.validate()
        for index, tab in enumerate(self.field_tabs):
            tab.refresh()
            tab.set_issues(issues)
            label = self.spec.tabs[index][0]
            self.tabs.setTabText(index, f"{label} ⚠" if tab.has_errors(issues) else label)
        if self.capability_tab is not None:
            self.capability_tab.refresh()
        self.output_tab.refresh()
        self.output_tab.set_issues(issues)
        self.manual_tab.refresh()
        self.manual_tab.set_issues(issues)
        self.deploy_tab.refresh()
        self.deploy_tab.set_issues(issues)
        self.remote_tab.refresh()
        self.remote_tab.set_issues(issues)

    # -- theme ------------------------------------------------------------
    def _apply_theme(self) -> None:
        tokens = theme.THEMES[self._theme]
        self.setStyleSheet(theme.stylesheet(tokens))
        app = QApplication.instance()
        if app is not None:
            app.setPalette(theme.build_palette(tokens))
        self.theme_button.setText("Dark mode" if self._theme == "light" else "Light mode")

    def _toggle_theme(self) -> None:
        self._theme = "dark" if self._theme == "light" else "light"
        self.settings.setValue(theme.SETTINGS_THEME_KEY, self._theme)
        self._apply_theme()

    # -- shutdown ---------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's spelling
        """Stop a running job before the window goes.

        Leaving the deploy running is not on offer: when this process exits the
        read end of its pipe closes, and the child dies on BrokenPipeError at
        whatever point it had reached. Better to end it deliberately.
        """
        running = [t for t in (self.deploy_tab, self.remote_tab)
                   if t.job is not None]
        if running:
            answer = QMessageBox.question(
                self, self.spec.app_name,
                "Something is still running.\n\nStop it and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for tab in running:
                tab._cancel()
                if tab.job is not None:
                    tab.job.wait(5000)
        super().closeEvent(event)


def build_app(spec: HostSpec, argv: list[str] | None = None) -> tuple[QApplication, BuilderWindow]:
    """Construct the application and window without running the event loop.

    Split out so the tests can drive a real window offscreen.
    """
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(spec.app_name)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    if spec.icon.is_file():
        app.setWindowIcon(QIcon(str(spec.icon)))
    return app, BuilderWindow(spec)


def run(spec: HostSpec) -> int:
    app, window = build_app(spec)
    window.show()
    return app.exec()
