"""Application shell: navigation rail, header, and the view stack."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame, QButtonGroup, QStatusBar, QSizePolicy,
)

from . import theme
from .instrument import Instrument
from .store import CaptureStore
from .transport import SerialLink
from .widgets import StatusChip
from .views.dashboard import DashboardView
from .views.analysis import AnalysisView
from .views.automation import AutomationView
from .views.terminal import TerminalView
from .views.settings import SettingsView

NAV_ITEMS = [
    ("channels", "CHANNELS"),
    ("measure", "MEASURE"),
    ("scripts", "SCRIPTS"),
    ("data", "DATA"),
    ("config", "CONFIG"),
]


class MainWindow(QMainWindow):
    def __init__(self, port=None, baud=None, autoconnect=False):
        super().__init__()
        self.setWindowTitle("Agilent 54622D -- Signal Processor Terminal")
        self.resize(1500, 940)

        self.link = SerialLink(self)
        self.instrument = Instrument(self.link, self)
        self.store = CaptureStore(self)

        self._build_ui()
        self._connect_signals()

        if port:
            self.settings_view.port.setCurrentText(port)
        if baud:
            index = self.settings_view.baud.findData(baud)
            if index >= 0:
                self.settings_view.baud.setCurrentIndex(index)

        if autoconnect:
            QTimer.singleShot(200, self._connect_from_settings)

    # -- construction ------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav())

        self.stack = QStackedWidget()
        self.dashboard = DashboardView(self.instrument)
        self.automation = AutomationView(self.instrument)
        self.terminal = TerminalView(self.instrument)
        self.analysis = AnalysisView(self.instrument, self.store)
        self.settings_view = SettingsView(self.instrument)

        for view in (self.dashboard, self.automation, self.terminal,
                     self.analysis, self.settings_view):
            self.stack.addWidget(view)

        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self.setCentralWidget(central)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.status_label = QLabel("Not connected")
        self.status_label.setFont(theme.mono_font(8))
        # Ignored horizontal policy: the label's text can never widen the
        # window, so a long error message scrolls out of view instead of
        # stretching the whole GUI.
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        status.addWidget(self.status_label, 1)
        self.setStatusBar(status)

    def _build_header(self):
        header = QFrame()
        header.setObjectName("appHeader")
        header.setFixedHeight(48)
        # Scoped to the frame itself: an unqualified rule here would cascade
        # into every child, flattening the accent buttons' background.
        header.setStyleSheet(
            f"QFrame#appHeader {{ background:{theme.SURFACE_LOWEST};"
            f" border-bottom:1px solid {theme.OUTLINE_VARIANT}; }}"
        )

        row = QHBoxLayout(header)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(10)

        title = QLabel("SIG-PROC TERMINAL")
        title.setFont(theme.mono_font(12, bold=True))
        title.setStyleSheet(f"color:{theme.PRIMARY_CONTAINER};")
        row.addWidget(title)

        subtitle = QLabel("AGILENT 54622D")
        subtitle.setFont(theme.mono_font(8))
        subtitle.setStyleSheet(f"color:{theme.OUTLINE};")
        row.addWidget(subtitle)

        row.addSpacing(20)
        row.addStretch(1)

        self.link_chip = StatusChip("DISCONNECTED", "error")
        row.addWidget(self.link_chip)

        self.run_chip = StatusChip("STOPPED", "neutral")
        row.addWidget(self.run_chip)

        self.autoset_button = QPushButton("AUTOSET")
        self.autoset_button.setObjectName("accent")
        self.autoset_button.setFont(theme.mono_font(9, bold=True))
        self.autoset_button.clicked.connect(self.instrument.autoscale)

        self.single_button = QPushButton("SINGLE")
        self.single_button.setFont(theme.mono_font(9, bold=True))
        self.single_button.clicked.connect(self.instrument.single)

        row.addWidget(self.autoset_button)
        row.addWidget(self.single_button)
        return header

    def _build_nav(self):
        rail = QFrame()
        rail.setObjectName("navRail")
        rail.setFixedWidth(78)

        column = QVBoxLayout(rail)
        column.setContentsMargins(0, 8, 0, 8)
        column.setSpacing(2)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        for index, (_key, label) in enumerate(NAV_ITEMS):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setFont(theme.mono_font(8, bold=True))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _, i=index: self._select_view(i))
            self._nav_group.addButton(button)
            column.addWidget(button)
            if index == 0:
                button.setChecked(True)

        column.addStretch(1)
        return rail

    def _select_view(self, index):
        self.stack.setCurrentIndex(index)
        # Keep the rail in step when navigation is driven programmatically.
        buttons = self._nav_group.buttons()
        if 0 <= index < len(buttons) and not buttons[index].isChecked():
            buttons[index].setChecked(True)
        if index == 1:
            self.automation.sync_scales()

    # -- wiring ------------------------------------------------------------

    def _connect_signals(self):
        link = self.link

        link.traffic.connect(self._on_traffic)
        link.link_changed.connect(self._on_link_changed)
        link.frame_ready.connect(self._on_frame)
        link.rate_changed.connect(self.dashboard.set_rate)
        link.replied.connect(self._on_reply)
        link.failed.connect(self._on_failed)
        link.bulk_progress.connect(self.analysis.on_bulk_progress)

        self.instrument.run_state_changed.connect(self._on_run_state)

        self.settings_view.connect_requested.connect(self._on_connect_requested)
        self.settings_view.disconnect_requested.connect(self._disconnect)
        self.settings_view.display_changed.connect(self._on_display_changed)

        self.analysis.status_message.connect(self.status_label.setText)

    def _on_traffic(self, direction, text):
        self.dashboard.console.append(direction, text)
        self.terminal.console.append(direction, text)
        if direction == "ERR":
            self.status_label.setText(text[:160])

    def _on_link_changed(self, connected, description):
        self.link_chip.set_state("CONNECTED" if connected else "DISCONNECTED",
                                 "ok" if connected else "error")
        self.settings_view.set_link_state(connected, description)
        self.status_label.setText(description if description else "Not connected")

        for widget in (self.autoset_button, self.single_button):
            widget.setEnabled(connected)
        self.dashboard.acq_button.setEnabled(connected)

    def _on_run_state(self, running):
        self.run_chip.set_state("RUNNING" if running else "STOPPED",
                                "ok" if running else "neutral")

    def _on_frame(self, source, times, values, _preamble):
        if source.startswith("POD"):
            self.dashboard.plot.update_pod(source, times, values)
            return

        self.dashboard.plot.update_analog(source, times, values)
        self.automation.update_trace(source, times, values)
        self.analysis.update_trace(source, times, values)

    def _on_reply(self, tag, payload):
        if tag == "display:bitmap":
            self.analysis.on_capture_finished(payload)

    def _on_failed(self, tag, reason):
        if tag == "display:bitmap":
            self.analysis.on_capture_failed()
            self.status_label.setText(f"Screen capture failed: {reason}")

    def _on_display_changed(self, key, value):
        plots = (self.dashboard.plot, self.automation.plot, self.analysis.plot)
        for plot in plots:
            if key == "grid":
                plot.set_grid_intensity(value)
            elif key == "width":
                plot.set_trace_width(value)
            elif key == "persistence":
                plot.set_persistence(value)
            elif key == "clear_persistence":
                plot.clear_persistence()

    # -- connection --------------------------------------------------------

    def _connect_from_settings(self):
        self.settings_view._on_connect_clicked()

    def _on_connect_requested(self, port, baud, timeout):
        if self.link.isRunning():
            self._disconnect()
        self.link.configure(port, baud, timeout)
        self.status_label.setText(f"Opening {port} at {baud} baud...")
        self.link.start()

    def _disconnect(self):
        self.instrument.stop()
        self.link.shutdown()
        self._on_link_changed(False, "disconnected")

    # -- shutdown ----------------------------------------------------------

    def closeEvent(self, event):
        if self.terminal.runner.running:
            self.terminal.runner.halt()
        self.link.set_streaming(False)
        self.link.shutdown()
        super().closeEvent(event)
