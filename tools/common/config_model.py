"""The builder's single piece of state: the config being assembled.

One nested dict shaped exactly like the deploy YAML, addressed by dotted paths,
emitting ``changed`` so the window can re-validate and re-render every tab in
one pass. Widgets never hold state of their own — they read the model in
``refresh()`` and write it on edit, which is what keeps a preset click and a
hand edit indistinguishable downstream.

Absence is meaningful here: a key that is not in the dict is a key that will not
be in the YAML, so deploy.py's own default applies. ``unset()`` therefore prunes
rather than writing None.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from typing import Any, Iterator

from PyQt6.QtCore import QObject, pyqtSignal

from validation import drop_path, get_path, set_path


class ConfigModel(QObject):
    """A nested config dict with change notification."""

    changed = pyqtSignal()

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__()
        self._data: dict[str, Any] = copy.deepcopy(initial or {})
        self._depth = 0
        self._dirty = False

    # -- reading ----------------------------------------------------------
    @property
    def data(self) -> dict:
        """A deep copy — callers must not mutate the model behind its back."""
        return copy.deepcopy(self._data)

    def get(self, path: str, default: Any = None) -> Any:
        value = get_path(self._data, path)
        return default if value is None else value

    # -- writing ----------------------------------------------------------
    def set(self, path: str, value: Any) -> None:
        if get_path(self._data, path) == value:
            return
        set_path(self._data, path, value)
        self._touch()

    def unset(self, path: str) -> None:
        if get_path(self._data, path) is None:
            return
        drop_path(self._data, path)
        self._touch()

    def set_or_unset(self, path: str, value: Any, *, omit_when: Any = "") -> None:
        """Write ``value``, or drop the key when it equals ``omit_when``.

        The workhorse for optional text fields: an empty box must leave no trace
        in the output rather than emitting ``key: ""``.
        """
        if value == omit_when:
            self.unset(path)
        else:
            self.set(path, value)

    def replace(self, data: dict) -> None:
        """Swap the whole config — how a preset is applied."""
        self._data = copy.deepcopy(data)
        self._touch()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Coalesce many writes into one ``changed`` emission."""
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._dirty:
                self._dirty = False
                self.changed.emit()

    def _touch(self) -> None:
        if self._depth:
            self._dirty = True
        else:
            self.changed.emit()
