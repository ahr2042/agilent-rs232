"""SCPI console: colour-coded TX/RX/INF/ERR log with a command entry line."""

import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QLineEdit,
    QPushButton, QLabel,
)

from . import theme

MAX_LINES = 2000


class ScpiConsole(QWidget):
    """Transaction log plus a direct command line."""

    command_entered = pyqtSignal(str)

    def __init__(self, show_entry=True, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono_font(9))
        self.log.setMaximumBlockCount(MAX_LINES)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log, 1)

        self._history = []
        self._history_index = 0

        if show_entry:
            row = QHBoxLayout()
            row.setSpacing(4)

            prompt = QLabel(">")
            prompt.setFont(theme.mono_font(11, bold=True))
            prompt.setStyleSheet(f"color:{theme.PRIMARY_CONTAINER};")

            self.entry = CommandEdit()
            self.entry.setPlaceholderText("Direct SCPI command...")
            self.entry.setFont(theme.mono_font(10))
            self.entry.returnPressed.connect(self._submit)
            self.entry.history_requested.connect(self._recall)

            send = QPushButton("SEND")
            send.setObjectName("accent")
            send.setFont(theme.mono_font(9, bold=True))
            send.clicked.connect(self._submit)

            row.addWidget(prompt)
            row.addWidget(self.entry, 1)
            row.addWidget(send)
            layout.addLayout(row)
        else:
            self.entry = None

    # -- logging -----------------------------------------------------------

    def append(self, direction, text):
        colour = theme.LOG_COLOURS.get(direction, theme.ON_SURFACE)
        stamp = time.strftime("%H:%M:%S")
        # Cap the line so a stray binary payload can neither bloat the widget
        # nor create a horizontal scroll region wider than the window.
        if len(text) > 200:
            text = text[:200] + f"... (+{len(text) - 200} chars)"
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        self.log.appendHtml(
            f'<span style="color:{theme.OUTLINE_VARIANT}">[{stamp}]</span> '
            f'<span style="color:{colour};font-weight:bold">{direction:3s}</span> '
            f'<span style="color:{colour}">{safe}</span>'
        )
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self):
        self.log.clear()

    def plain_text(self):
        return self.log.toPlainText()

    # -- entry -------------------------------------------------------------

    def _submit(self):
        if not self.entry:
            return
        text = self.entry.text().strip()
        if not text:
            return
        self._history.append(text)
        self._history_index = len(self._history)
        self.entry.clear()
        self.command_entered.emit(text)

    def _recall(self, delta):
        if not self._history:
            return
        self._history_index = max(0, min(len(self._history) - 1,
                                         self._history_index + delta))
        self.entry.setText(self._history[self._history_index])


class CommandEdit(QLineEdit):
    """Line edit with up/down command history."""

    history_requested = pyqtSignal(int)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Up:
            self.history_requested.emit(-1)
            event.accept()
        elif event.key() == Qt.Key.Key_Down:
            self.history_requested.emit(1)
            event.accept()
        else:
            super().keyPressEvent(event)
