"""
SCPI Scripting Terminal.

The script language is the one shown in the design: bare SCPI lines plus
FOR/NEXT, WAIT and PRINT. It is interpreted by a timer-driven state machine
rather than a loop, so a running script never blocks the GUI and HALT takes
effect immediately.
"""

import re
import time

from PyQt6.QtCore import Qt, QRegularExpression, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QSyntaxHighlighter, QTextCharFormat, QColor, QFont,
)
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QTreeWidget, QTreeWidgetItem, QSplitter, QLineEdit, QFileDialog,
)

from .. import theme
from ..console import ScpiConsole
from ..protocol import COMMAND_LIBRARY
from ..widgets import Panel, StatusChip

DEFAULT_SCRIPT = """\
# SCPI automation script
# Initialise the instrument
*IDN?
:CHANnel1:DISPlay ON
:CHANnel1:SCALe 5.0E-1
:TRIGger:SWEep AUTO

# Loop a measurement
FOR i = 1 TO 10
  :MEASure:VPP? CHANnel1
  WAIT 500
NEXT i

PRINT batch complete
"""


class ScpiHighlighter(QSyntaxHighlighter):
    """Colours SCPI mnemonics, control keywords, numbers and comments."""

    def __init__(self, document):
        super().__init__(document)

        def fmt(colour, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(colour))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        self._rules = [
            # SCPI command paths and common commands
            (QRegularExpression(r"[:\*][A-Za-z][A-Za-z0-9:\*]*\??"),
             fmt(theme.SECONDARY, bold=True)),
            # Control keywords
            (QRegularExpression(r"\b(FOR|NEXT|TO|WAIT|PRINT|IF|END)\b"),
             fmt(theme.PRIMARY_CONTAINER, bold=True)),
            # Numbers, including scientific notation
            (QRegularExpression(r"\b\d+\.?\d*([eE][+-]?\d+)?\b"),
             fmt(theme.TERTIARY)),
            # ON / OFF and similar literals
            (QRegularExpression(r"\b(ON|OFF|AUTO|NORMal|NORM|EDGE|BMP)\b"),
             fmt(theme.ON_SURFACE_VARIANT)),
            # Strings
            (QRegularExpression(r"\"[^\"]*\""), fmt(theme.TERTIARY_CONTAINER)),
            # Comments last so they win
            (QRegularExpression(r"#.*$"), fmt(theme.OUTLINE, italic=True)),
        ]

    def highlightBlock(self, text):
        for pattern, style in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), style)


class ScriptRunner(QWidget):
    """Executes the script one statement per timer tick."""

    log = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self._lines = []
        self._pc = 0
        self._loops = []
        self._resume_at = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._step)

    @property
    def running(self):
        return self._timer.isActive()

    def start(self, text):
        self._lines = text.splitlines()
        self._pc = 0
        self._loops = []
        self._resume_at = 0.0
        self.log.emit("INF", f"script started ({len(self._lines)} lines)")
        self._timer.start()

    def halt(self):
        if self._timer.isActive():
            self._timer.stop()
            self.log.emit("ERR", f"script halted at line {self._pc}")
            self.finished.emit()

    def _finish(self):
        self._timer.stop()
        self.log.emit("INF", "script complete")
        self.finished.emit()

    def _step(self):
        if time.monotonic() < self._resume_at:
            return

        if self._pc >= len(self._lines):
            self._finish()
            return

        raw = self._lines[self._pc]
        self._pc += 1
        line = raw.split("#", 1)[0].strip()
        if not line:
            return

        # FOR i = 1 TO 10
        match = re.match(r"^FOR\s+(\w+)\s*=\s*(-?\d+)\s+TO\s+(-?\d+)$", line, re.I)
        if match:
            name, start, stop = match.group(1), int(match.group(2)), int(match.group(3))
            self._loops.append({"var": name, "value": start, "stop": stop,
                                "head": self._pc})
            if start > stop:
                self._skip_to_next(name)
            return

        # NEXT i
        match = re.match(r"^NEXT\s*(\w*)$", line, re.I)
        if match:
            if not self._loops:
                self.log.emit("ERR", f"NEXT without FOR on line {self._pc}")
                self._finish()
                return
            loop = self._loops[-1]
            loop["value"] += 1
            if loop["value"] <= loop["stop"]:
                self._pc = loop["head"]
            else:
                self._loops.pop()
            return

        # WAIT 500   (milliseconds)
        match = re.match(r"^WAIT\s+(\d+)\s*(ms|s)?$", line, re.I)
        if match:
            amount = int(match.group(1))
            seconds = amount / 1000.0 if (match.group(2) or "ms").lower() == "ms" else amount
            self._resume_at = time.monotonic() + seconds
            return

        # PRINT some text
        match = re.match(r"^PRINT\s+(.*)$", line, re.I)
        if match:
            self.log.emit("INF", match.group(1).strip('"'))
            return

        if line.startswith((":", "*")):
            self.instrument.send_raw(line)
            return

        self.log.emit("ERR", f"line {self._pc}: cannot parse {line!r}")

    def _skip_to_next(self, name):
        """Jump past a loop body whose range is empty."""
        depth = 1
        while self._pc < len(self._lines):
            text = self._lines[self._pc].split("#", 1)[0].strip()
            self._pc += 1
            if re.match(r"^FOR\s+", text, re.I):
                depth += 1
            elif re.match(r"^NEXT\b", text, re.I):
                depth -= 1
                if depth == 0:
                    break
        if self._loops:
            self._loops.pop()


class TerminalView(QWidget):
    """Script editor, debug console and command library."""

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument

        self.runner = ScriptRunner(instrument, self)
        self.runner.log.connect(self._on_runner_log)
        self.runner.finished.connect(self._on_runner_finished)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_editor())
        splitter.addWidget(self._build_console())
        splitter.addWidget(self._build_library())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([440, 460, 300])
        root.addWidget(splitter)

    # -- editor ------------------------------------------------------------

    def _build_editor(self):
        panel = Panel("Script")

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self.run_button = QPushButton("RUN SCRIPT")
        self.run_button.setObjectName("accent")
        self.run_button.setFont(theme.mono_font(9, bold=True))
        self.run_button.clicked.connect(self._run)

        self.halt_button = QPushButton("HALT")
        self.halt_button.setObjectName("danger")
        self.halt_button.setFont(theme.mono_font(9, bold=True))
        self.halt_button.setEnabled(False)
        self.halt_button.clicked.connect(self.runner.halt)

        load = QPushButton("OPEN")
        load.setFont(theme.mono_font(9))
        load.clicked.connect(self._open)

        save = QPushButton("SAVE")
        save.setFont(theme.mono_font(9))
        save.clicked.connect(self._save)

        for widget in (self.run_button, self.halt_button, load, save):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        panel.body.addLayout(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setFont(theme.mono_font(10))
        self.editor.setPlainText(DEFAULT_SCRIPT)
        self.editor.setTabStopDistance(28)
        self._highlighter = ScpiHighlighter(self.editor.document())
        self.editor.cursorPositionChanged.connect(self._update_position)
        panel.body.addWidget(self.editor, 1)

        status = QHBoxLayout()
        self.position_label = QLabel("LINE: 1, COL: 1")
        self.position_label.setFont(theme.mono_font(8))
        self.position_label.setStyleSheet(f"color:{theme.OUTLINE};")
        self.script_status = StatusChip("IDLE", "neutral")
        status.addWidget(self.position_label)
        status.addStretch(1)
        status.addWidget(self.script_status)
        panel.body.addLayout(status)

        return panel

    def _build_console(self):
        panel = Panel("Debug console")

        toolbar = QHBoxLayout()
        clear = QPushButton("CLEAR")
        clear.setFont(theme.mono_font(9))
        clear.clicked.connect(lambda: self.console.clear())
        save = QPushButton("SAVE LOG")
        save.setFont(theme.mono_font(9))
        save.clicked.connect(self._save_log)
        toolbar.addWidget(clear)
        toolbar.addWidget(save)
        toolbar.addStretch(1)
        panel.body.addLayout(toolbar)

        self.console = ScpiConsole()
        self.console.command_entered.connect(self.instrument.send_raw)
        panel.body.addWidget(self.console, 1)
        return panel

    def _build_library(self):
        panel = Panel("Command library")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands...")
        self.search.setFont(theme.mono_font(9))
        self.search.textChanged.connect(self._filter_library)
        panel.body.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFont(theme.mono_font(9))
        self.tree.itemDoubleClicked.connect(self._insert_command)

        for group, entries in COMMAND_LIBRARY.items():
            parent = QTreeWidgetItem([group])
            parent.setFont(0, theme.mono_font(9, bold=True))
            parent.setForeground(0, QColor(theme.ON_SURFACE_VARIANT))
            for command, description in entries:
                child = QTreeWidgetItem([command])
                child.setToolTip(0, description)
                child.setForeground(0, QColor(theme.SECONDARY))
                child.setData(0, Qt.ItemDataRole.UserRole, command)
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)

        self.tree.expandAll()
        panel.body.addWidget(self.tree, 1)

        hint = QLabel("Double-click to insert at the cursor.")
        hint.setFont(theme.mono_font(8))
        hint.setStyleSheet(f"color:{theme.OUTLINE};")
        panel.body.addWidget(hint)
        return panel

    # -- behaviour ---------------------------------------------------------

    def _run(self):
        if self.runner.running:
            return
        self.run_button.setEnabled(False)
        self.halt_button.setEnabled(True)
        self.script_status.set_state("RUNNING", "ok")
        self.runner.start(self.editor.toPlainText())

    def _on_runner_finished(self):
        self.run_button.setEnabled(True)
        self.halt_button.setEnabled(False)
        self.script_status.set_state("IDLE", "neutral")

    def _on_runner_log(self, direction, text):
        self.console.append(direction, text)

    def _update_position(self):
        cursor = self.editor.textCursor()
        self.position_label.setText(
            f"LINE: {cursor.blockNumber() + 1}, COL: {cursor.positionInBlock() + 1}")

    def _insert_command(self, item, _column):
        command = item.data(0, Qt.ItemDataRole.UserRole)
        if command:
            self.editor.insertPlainText(command)
            self.editor.setFocus()

    def _filter_library(self, text):
        needle = text.strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(index)
            visible_children = 0
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                match = (not needle
                         or needle in child.text(0).lower()
                         or needle in child.toolTip(0).lower())
                child.setHidden(not match)
                visible_children += int(match)
            parent.setHidden(visible_children == 0)

    def _open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open script", "", "Scripts (*.scpi *.txt);;All files (*)")
        if path:
            with open(path) as handle:
                self.editor.setPlainText(handle.read())

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save script", "script.scpi", "Scripts (*.scpi *.txt)")
        if path:
            with open(path, "w") as handle:
                handle.write(self.editor.toPlainText())

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save debug log", "session.log", "Log files (*.log *.txt)")
        if path:
            with open(path, "w") as handle:
                handle.write(self.console.plain_text())
