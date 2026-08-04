"""Theme tokens, stylesheet template, and palette construction.

Shared by every host builder: one token vocabulary (#title #subtitle #details
#status #card #log #primaryButton #secondaryButton #successBadge
#warningBadge) plus the form controls the builders need — preset chips,
checkboxes, spin boxes and inline error text.

A missing token is a startup KeyError, so launching is itself a completeness
check on both palettes.

Ported from enigma's tools/enigma_builder/theme.py. The palette work alongside
the stylesheet is not redundant: Fusion draws combo arrows, scrollbars and
placeholder text from the palette, not from QSS.
"""

from __future__ import annotations

from string import Template

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette


SETTINGS_THEME_KEY = "ui/theme"

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "window_bg": "#f5f7fb",
        "text": "#172033",
        "title_text": "#101828",
        "muted_text": "#667085",
        "card_bg": "white",
        "card_border": "#e4e7ec",
        "input_bg": "white",
        "input_border": "#d0d5dd",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "on_accent": "white",
        "log_bg": "white",
        "log_text": "#172033",
        "log_border": "1px solid #d0d5dd",
        "button_bg": "white",
        "button_hover_bg": "#f9fafb",
        "disabled_text": "#98a2b3",
        "disabled_bg": "#f2f4f7",
        "danger_text": "#b42318",
        "progress_track": "#eaecf0",
        "success_text": "#027a48",
        "success_bg": "#ecfdf3",
        "warning_text": "#b54708",
        "warning_bg": "#fffaeb",
    },
    "dark": {
        "window_bg": "#0f1420",
        "text": "#e5e9f0",
        "title_text": "#f4f6fb",
        "muted_text": "#8a93a6",
        "card_bg": "#1a2130",
        "card_border": "#2a3346",
        "input_bg": "#141b29",
        "input_border": "#323d54",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "on_accent": "#ffffff",
        "log_bg": "#101828",
        "log_text": "#eaecf0",
        "log_border": "1px solid #2a3346",
        "button_bg": "#202a3c",
        "button_hover_bg": "#273349",
        "disabled_text": "#5b6478",
        "disabled_bg": "#171e2c",
        "danger_text": "#f97066",
        "progress_track": "#232c40",
        "success_text": "#4ade80",
        "success_bg": "#12271b",
        "warning_text": "#fbbf24",
        "warning_bg": "#2b2312",
    },
}

QSS_TEMPLATE = """
QMainWindow, QWidget {
    background: $window_bg;
    color: $text;
    font-size: 14px;
}
/* Labels and boxes sit *inside* cards, so they must not repaint the window
   background over the card they are on. */
QLabel, QCheckBox, QRadioButton {
    background: transparent;
}
QLabel#title {
    color: $title_text;
}
QLabel#subtitle, QLabel#details {
    color: $muted_text;
}
QLabel#status {
    font-size: 15px;
    font-weight: 600;
}
QFrame#card {
    background: $card_bg;
    border: 1px solid $card_border;
    border-radius: 12px;
}
QLineEdit, QComboBox, QPlainTextEdit {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 7px;
    padding: 8px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid $accent;
}
QComboBox QAbstractItemView {
    background: $input_bg;
    color: $text;
    border: 1px solid $input_border;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QPlainTextEdit#log {
    font-family: Consolas, "SFMono-Regular", monospace;
    background: $log_bg;
    color: $log_text;
    border: $log_border;
    padding: 12px;
}
QPushButton {
    background: $button_bg;
    border: 1px solid $input_border;
    border-radius: 7px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background: $button_hover_bg;
}
QPushButton:disabled {
    color: $disabled_text;
    background: $disabled_bg;
}
QPushButton#primaryButton {
    background: $accent;
    color: $on_accent;
    border: 1px solid $accent;
    padding-left: 22px;
    padding-right: 22px;
}
QPushButton#primaryButton:hover {
    background: $accent_hover;
}
QPushButton#secondaryButton {
    color: $danger_text;
}
QPushButton#themeToggle {
    background: transparent;
    border: none;
    color: $muted_text;
    font-weight: 600;
    padding: 4px 10px;
}
QPushButton#themeToggle:hover {
    color: $text;
    background: $button_hover_bg;
    border-radius: 7px;
}
QProgressBar {
    background: $progress_track;
    border: none;
    border-radius: 6px;
    height: 14px;
    text-align: center;
}
QProgressBar::chunk {
    background: $accent;
    border-radius: 6px;
}
QLabel#successBadge, QLabel#warningBadge {
    border-radius: 9px;
    padding: 4px 8px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#successBadge {
    color: $success_text;
    background: $success_bg;
}
QLabel#warningBadge {
    color: $warning_text;
    background: $warning_bg;
}
QScrollArea {
    background: transparent;
    border: none;
}
QSlider::groove:horizontal {
    background: $progress_track;
    border: none;
    border-radius: 3px;
    height: 6px;
}
QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 3px;
}
QSlider::sub-page:horizontal:disabled {
    background: $disabled_text;
}
QSlider::handle:horizontal {
    background: $card_bg;
    border: 2px solid $accent;
    width: 14px;
    margin: -6px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:disabled {
    background: $disabled_bg;
    border: 2px solid $disabled_text;
}
QTabWidget::pane {
    border: none;
    border-top: 1px solid $card_border;
}
QTabWidget::tab-bar {
    left: 0px;
}
QTabBar::tab {
    background: transparent;
    color: $muted_text;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 18px;
    margin-right: 2px;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    color: $text;
    background: $button_hover_bg;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}
QTabBar::tab:selected {
    color: $accent;
    border-bottom: 2px solid $accent;
}
QSpinBox {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 7px;
    padding: 6px 8px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QSpinBox:focus {
    border: 1px solid $accent;
}
QSpinBox:disabled {
    color: $disabled_text;
    background: $disabled_bg;
}
QCheckBox, QRadioButton {
    spacing: 8px;
    padding: 2px 0;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: $disabled_text;
}
/* Styling any QCheckBox property makes Qt stop drawing the native indicator,
   so the box has to be described here in full — otherwise "off" renders as
   blank space and cannot be told apart from a missing control. On/off is
   carried by the accent fill rather than a glyph, since QSS can only put an
   image inside an indicator. */
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid $input_border;
    background: $input_bg;
}
QCheckBox::indicator {
    border-radius: 4px;
}
QRadioButton::indicator {
    border-radius: 8px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border: 1px solid $accent;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: $accent;
    border: 1px solid $accent;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background: $disabled_bg;
    border: 1px solid $disabled_text;
}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {
    background: $disabled_text;
    border: 1px solid $disabled_text;
}
QLabel#sectionTitle {
    color: $title_text;
    font-size: 15px;
    font-weight: 700;
}
QLabel#errorText {
    color: $danger_text;
    font-size: 12px;
}
QLabel#gateHint {
    color: $muted_text;
    font-size: 12px;
    padding-bottom: 4px;
}
QPushButton#presetChip {
    padding: 7px 18px;
    border-radius: 16px;
}
QPushButton#presetChip:checked {
    background: $accent;
    color: $on_accent;
    border: 1px solid $accent;
}
QPushButton#linkButton {
    background: transparent;
    border: none;
    color: $accent;
    font-weight: 600;
    padding: 2px 6px;
}
QPushButton#linkButton:hover {
    color: $accent_hover;
    text-decoration: underline;
}
QPushButton#linkButton:disabled {
    color: $disabled_text;
    background: transparent;
}
QPlainTextEdit#preview {
    font-family: Consolas, "SFMono-Regular", monospace;
    font-size: 12px;
    background: $log_bg;
    color: $log_text;
    border: $log_border;
    padding: 12px;
}
QFrame#separator {
    background: $card_border;
    max-height: 1px;
    border: none;
}
"""


def detect_system_theme() -> str:
    """Return "dark" or "light" from the OS color scheme (Unknown counts as light)."""
    scheme = QGuiApplication.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def build_palette(tokens: dict[str, str]) -> QPalette:
    """QPalette matching the QSS tokens, for Fusion-drawn primitives.

    Checkbox indicators, combo arrows, scrollbars, and placeholder text are
    rendered from the palette rather than the stylesheet.
    """
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(tokens["window_bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(tokens["text"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(tokens["input_bg"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(tokens["button_bg"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens["muted_text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens["card_bg"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens["text"]))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(tokens["disabled_text"]),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(tokens["disabled_text"]),
    )
    return palette


def stylesheet(tokens: dict[str, str]) -> str:
    """Substitute a token set into the QSS template.

    A missing token raises KeyError here, which is why applying both themes at
    startup (the tests do) is a completeness check on both palettes.
    """
    return Template(QSS_TEMPLATE).substitute(tokens)
