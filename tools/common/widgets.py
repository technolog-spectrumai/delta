"""Small shared widgets and layout helpers, shared by every host builder.

Keeps the page rhythm identical across tabs: cards with 20px padding, a
scrolling form area whose action row never scrolls away, muted hint text under
the control it explains, and inline error labels that stay hidden until they
have something to say.

Ported from enigma's tools/enigma_builder/widgets.py, which is where this
vocabulary comes from. Living here rather than in one builder is the point:
enigma keeps two copies of its chrome in sync by hand, and we would rather not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

INT_MAX = 2_147_483_647


def card(spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    """A themed card and its vertical layout."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(spacing)
    return frame, layout


def form_card() -> tuple[QFrame, QFormLayout]:
    """A themed card laid out as a right-aligned form."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QFormLayout(frame)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setHorizontalSpacing(18)
    layout.setVerticalSpacing(14)
    layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return frame, layout


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def hint(text: str, object_name: str = "details") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    return label


def badge(text: str, ok: bool) -> QLabel:
    label = QLabel(text)
    label.setObjectName("successBadge" if ok else "warningBadge")
    return label


def set_badge(label: QLabel, text: str, ok: bool) -> None:
    """Retarget an existing badge between the success and warning styles.

    Qt does not re-evaluate a stylesheet when objectName changes, so the
    unpolish/polish pair is mandatory — without it the pill keeps its old
    colours. enigma repeats this incantation at five call sites; here it lives
    in one place.
    """
    label.setText(text)
    label.setObjectName("successBadge" if ok else "warningBadge")
    style = label.style()
    style.unpolish(label)
    style.polish(label)


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def link_button(text: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("linkButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.clicked.connect(lambda: on_click())
    return button


class ErrorLabel(QLabel):
    """Inline validation text; invisible (and taking no space) when empty."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("errorText")
        self.setWordWrap(True)
        self.hide()

    def set_messages(self, messages: list[str]) -> None:
        text = "\n".join(messages)
        self.setText(text)
        self.setVisible(bool(text))


def scroll_page(outer: QVBoxLayout) -> QVBoxLayout:
    """Add a scrolling page to ``outer`` and return the page's layout.

    The form scrolls; whatever the caller adds to ``outer`` afterwards (a
    preview pane, an action row) stays pinned, so the primary button is never
    scrolled off the bottom on a small window.
    """
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidget(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(scroll, 1)

    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 12, 0, 0)
    layout.setSpacing(16)
    return layout


def page_layout(widget: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(16)
    return layout


def spin(minimum: int = 0, maximum: int = INT_MAX, width: int = 130) -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setGroupSeparatorShown(True)
    # Without this, clamping on every keystroke rewrites the field mid-typing.
    box.setKeyboardTracking(False)
    box.setMinimumWidth(width)
    return box


class PathRow(QWidget):
    """A path field with a Browse button, and optionally a "default" link.

    enigma open-codes this shape three times (repo picker, output folder, icon
    file); every builder needs at least the folder flavour, so it is a widget
    here. ``directory=False`` switches Browse to a file picker.
    """

    changed = pyqtSignal(str)

    def __init__(
        self,
        caption: str,
        *,
        directory: bool = True,
        file_filter: str = "",
        on_default: Callable[[], str] | None = None,
    ) -> None:
        super().__init__()
        self._caption = caption
        self._directory = directory
        self._file_filter = file_filter

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self.changed.emit)
        row.addWidget(self.edit, 1)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)

        if on_default is not None:
            row.addWidget(link_button("default", lambda: self.set_value(on_default())))

    def value(self) -> str:
        return self.edit.text().strip()

    def path(self) -> Path | None:
        text = self.value()
        return Path(text).expanduser() if text else None

    def set_value(self, text: str) -> None:
        if self.edit.text() != text:
            self.edit.setText(text)

    def _browse(self) -> None:
        start = self.value() or str(Path.home())
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(self, self._caption, start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self, self._caption, start, self._file_filter
            )
        if chosen:
            self.set_value(chosen)


class MonoPane(QPlainTextEdit):
    """Read-only monospace pane — the YAML preview and the build log."""

    def __init__(self, object_name: str, placeholder: str, max_blocks: int) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setReadOnly(True)
        self.setPlaceholderText(placeholder)
        self.setMaximumBlockCount(max_blocks)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def append_line(self, message: str) -> None:
        cleaned = message.rstrip()
        if not cleaned:
            return
        self.appendPlainText(cleaned)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_content(self, text: str) -> None:
        """Replace the content, keeping the scroll position.

        The preview re-renders on every keystroke; without this the pane would
        jump to the top while you are reading the section you just edited.
        """
        scrollbar = self.verticalScrollBar()
        position = scrollbar.value()
        self.setPlainText(text)
        scrollbar.setValue(min(position, scrollbar.maximum()))
