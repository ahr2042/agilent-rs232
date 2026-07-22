"""
Settings view.

The design's network panel (static IP, TCP port, auto-reconnect) does not
apply to an RS-232 instrument, so it is replaced by the serial parameters
that actually govern the link. Display settings are purely local and cost
no bandwidth.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSlider, QCheckBox, QDoubleSpinBox, QMessageBox, QGridLayout,
)

from .. import theme
from ..protocol import BAUD_RATES
from ..transport import available_ports
from ..widgets import Panel, SectionLabel, StatusChip, labelled


class SettingsView(QWidget):
    """Serial link configuration, display preferences and maintenance."""

    connect_requested = pyqtSignal(str, int, float)
    disconnect_requested = pyqtSignal()
    display_changed = pyqtSignal(str, object)

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        column = QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(self._build_connection())
        column.addWidget(self._build_maintenance())
        column.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self._build_display())
        right.addWidget(self._build_notes())
        right.addStretch(1)

        root.addLayout(column, 1)
        root.addLayout(right, 1)

    # -- connection --------------------------------------------------------

    def _build_connection(self):
        panel = Panel("Connection protocol")

        self.port = QComboBox()
        self.port.setEditable(True)
        self._reload_ports()
        panel.body.addLayout(labelled("Serial port", self.port))

        self.baud = QComboBox()
        for rate in BAUD_RATES:
            self.baud.addItem(str(rate), rate)
        self.baud.setCurrentIndex(len(BAUD_RATES) - 1)
        panel.body.addLayout(labelled("Baud rate", self.baud))

        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0.2, 30.0)
        self.timeout.setValue(2.0)
        self.timeout.setSuffix(" s")
        panel.body.addLayout(labelled("Response timeout", self.timeout))

        row = QHBoxLayout()
        refresh = QPushButton("RESCAN PORTS")
        refresh.setFont(theme.mono_font(9))
        refresh.clicked.connect(self._reload_ports)

        self.connect_button = QPushButton("CONNECT")
        self.connect_button.setObjectName("accent")
        self.connect_button.setFont(theme.mono_font(10, bold=True))
        self.connect_button.setMinimumHeight(32)
        self.connect_button.clicked.connect(self._on_connect_clicked)

        row.addWidget(refresh)
        row.addWidget(self.connect_button, 1)
        panel.body.addLayout(row)

        self.state_chip = StatusChip("DISCONNECTED", "error")
        panel.body.addWidget(self.state_chip)

        self.identity = QLabel("--")
        self.identity.setWordWrap(True)
        self.identity.setFont(theme.mono_font(8))
        self.identity.setStyleSheet(f"color:{theme.OUTLINE};")
        panel.body.addWidget(self.identity)

        note = QLabel(
            "8N1 with DTR/DSR hardware handshaking, as the 546xx series "
            "requires. 57600 is the highest rate the instrument supports."
        )
        note.setWordWrap(True)
        note.setFont(theme.mono_font(8))
        note.setStyleSheet(f"color:{theme.OUTLINE};")
        panel.body.addWidget(note)

        return panel

    def _reload_ports(self):
        current = self.port.currentText()
        self.port.clear()
        for device, description in available_ports():
            self.port.addItem(f"{device}  ({description})", device)
        if self.port.count() == 0:
            self.port.addItem("/dev/ttyUSB0", "/dev/ttyUSB0")
        if current:
            self.port.setCurrentText(current)

    def _selected_port(self):
        data = self.port.currentData()
        return data or self.port.currentText().split()[0]

    def _on_connect_clicked(self):
        if self.connect_button.text() == "CONNECT":
            self.connect_requested.emit(
                self._selected_port(), int(self.baud.currentData()), self.timeout.value())
        else:
            self.disconnect_requested.emit()

    def set_link_state(self, connected, description):
        if connected:
            self.state_chip.set_state("CONNECTED", "ok")
            self.connect_button.setText("DISCONNECT")
            self.identity.setText(description)
        else:
            self.state_chip.set_state("DISCONNECTED", "error")
            self.connect_button.setText("CONNECT")
            self.identity.setText(description or "--")

    # -- display -----------------------------------------------------------

    def _build_display(self):
        panel = Panel("Display & persistence")

        self.grid = QSlider(Qt.Orientation.Horizontal)
        self.grid.setRange(2, 100)
        self.grid.setValue(35)
        self.grid_label = QLabel("35%")
        self.grid_label.setObjectName("readoutSmall")
        self.grid.valueChanged.connect(self._on_grid)
        row = QHBoxLayout()
        row.addWidget(SectionLabel("Grid intensity"))
        row.addStretch(1)
        row.addWidget(self.grid_label)
        panel.body.addLayout(row)
        panel.body.addWidget(self.grid)

        self.width = QSlider(Qt.Orientation.Horizontal)
        self.width.setRange(5, 40)      # tenths of a pixel
        self.width.setValue(15)
        self.width_label = QLabel("1.5 px")
        self.width_label.setObjectName("readoutSmall")
        self.width.valueChanged.connect(self._on_width)
        row = QHBoxLayout()
        row.addWidget(SectionLabel("Trace thickness"))
        row.addStretch(1)
        row.addWidget(self.width_label)
        panel.body.addLayout(row)
        panel.body.addWidget(self.width)

        self.persistence = QCheckBox("Infinite persistence")
        self.persistence.toggled.connect(
            lambda on: self.display_changed.emit("persistence", on))
        panel.body.addWidget(self.persistence)

        clear = QPushButton("CLEAR PERSISTENCE")
        clear.setFont(theme.mono_font(9))
        clear.clicked.connect(lambda: self.display_changed.emit("clear_persistence", None))
        panel.body.addWidget(clear)

        return panel

    def _on_grid(self, value):
        self.grid_label.setText(f"{value}%")
        self.display_changed.emit("grid", value)

    def _on_width(self, value):
        self.width_label.setText(f"{value / 10:.1f} px")
        self.display_changed.emit("width", value / 10)

    # -- maintenance -------------------------------------------------------

    def _build_maintenance(self):
        panel = Panel("Maintenance & diagnostics")

        errors = QPushButton("READ ERROR QUEUE")
        errors.setFont(theme.mono_font(9))
        errors.clicked.connect(self.instrument.query_errors)
        panel.body.addWidget(errors)

        clear = QPushButton("CLEAR STATUS (*CLS)")
        clear.setFont(theme.mono_font(9))
        clear.clicked.connect(self.instrument.clear_status)
        panel.body.addWidget(clear)

        reset = QPushButton("FACTORY RESET (*RST)")
        reset.setObjectName("danger")
        reset.setFont(theme.mono_font(9, bold=True))
        reset.clicked.connect(self._confirm_reset)
        panel.body.addWidget(reset)

        return panel

    def _confirm_reset(self):
        answer = QMessageBox.question(
            self, "Reset instrument",
            "Send *RST? This returns the scope to its default setup and "
            "discards the current front-panel configuration.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.instrument.reset()

    # -- notes -------------------------------------------------------------

    def _build_notes(self):
        panel = Panel("Link budget")

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)

        rows = [
            ("Wire rate at 57600 8N1", "5760 B/s"),
            ("500 pts, BYTE", "~10 frames/s"),
            ("1000 pts, BYTE", "~5.7 frames/s"),
            ("1000 pts, WORD", "~2.9 frames/s"),
            ("2000 pts, WORD", "~1.4 frames/s"),
            ("Screen bitmap (~170 kB)", "~30 s per frame"),
        ]
        for row, (label, value) in enumerate(rows):
            left = QLabel(label)
            left.setFont(theme.mono_font(8))
            left.setStyleSheet(f"color:{theme.OUTLINE};")
            right = QLabel(value)
            right.setFont(theme.mono_font(8, bold=True))
            tone = theme.ERROR if "30 s" in value else theme.TERTIARY
            right.setStyleSheet(f"color:{tone};")
            grid.addWidget(left, row, 0)
            grid.addWidget(right, row, 1, Qt.AlignmentFlag.AlignRight)

        panel.body.addLayout(grid)

        note = QLabel(
            "The screen bitmap is a still capture, not a live view: at 57600 "
            "baud it is roughly 170x slower than transferring the samples and "
            "rebuilding the trace locally, which is what this application does."
        )
        note.setWordWrap(True)
        note.setFont(theme.mono_font(8))
        note.setStyleSheet(f"color:{theme.OUTLINE};")
        panel.body.addWidget(note)

        return panel
