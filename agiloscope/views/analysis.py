"""Analysis & Data Hub: cursors, FFT, and the capture/export repository."""

import csv
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSplitter, QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QProgressBar, QGridLayout, QStackedWidget,
)

from .. import theme
from ..measure import spectrum
from ..plot import WaveformPlot
from ..protocol import ANALOG_CHANNELS, channel_label, format_si
from ..widgets import Panel, Segmented, SectionLabel


class AnalysisView(QWidget):
    """Cursor measurements, local FFT, and stored captures."""

    status_message = pyqtSignal(str)

    def __init__(self, instrument, store, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self.store = store

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_centre())
        splitter.addWidget(self._build_repository())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 320])
        root.addWidget(splitter)

        store.changed.connect(self._refresh_repository)
        self._refresh_repository()

    # -- centre ------------------------------------------------------------

    def _build_centre(self):
        panel = Panel(flat=True)
        panel.body.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self.mode = Segmented(
            [("TIME", "TIME"), ("FFT", "MATH: FFT")], current="TIME")
        self.mode.changed.connect(self._on_mode)
        toolbar.addWidget(self.mode)

        self.cursor_toggle = QPushButton("CURSORS")
        self.cursor_toggle.setObjectName("segment")
        self.cursor_toggle.setCheckable(True)
        self.cursor_toggle.setFont(theme.mono_font(9))
        self.cursor_toggle.toggled.connect(self._on_cursors)
        toolbar.addWidget(self.cursor_toggle)

        self.cursor_source = QComboBox()
        for source in ANALOG_CHANNELS:
            self.cursor_source.addItem(channel_label(source), source)
        self.cursor_source.currentIndexChanged.connect(self._update_cursor_readout)
        toolbar.addWidget(self.cursor_source)

        self.window = QComboBox()
        for name in ("hann", "hamming", "blackman", "rectangular"):
            self.window.addItem(name.title(), name)
        self.window.currentIndexChanged.connect(self._recompute_fft)
        self.window.setVisible(False)
        toolbar.addWidget(self.window)

        toolbar.addStretch(1)

        export = QPushButton("EXPORT CSV")
        export.setFont(theme.mono_font(9))
        export.clicked.connect(self._export_csv)
        toolbar.addWidget(export)

        panel.body.addLayout(toolbar)

        self.stack = QStackedWidget()
        self.plot = WaveformPlot()
        self.plot.cursors_moved.connect(lambda *_: self._update_cursor_readout())
        self.stack.addWidget(self.plot)

        self.fft_plot = pg.PlotWidget()
        self.fft_plot.setBackground(theme.SURFACE_LOWEST)
        self.fft_plot.showGrid(x=True, y=True, alpha=0.35)
        self.fft_plot.setLabel("bottom", "Frequency", units="Hz")
        self.fft_plot.setLabel("left", "Amplitude", units="dBV")
        self.fft_curve = self.fft_plot.plot(pen=pg.mkPen(theme.SECONDARY, width=1.4))
        self.stack.addWidget(self.fft_plot)

        panel.body.addWidget(self.stack, 1)
        panel.body.addWidget(self._build_readouts())
        return panel

    def _build_readouts(self):
        frame = Panel(flat=True)
        frame.setMaximumHeight(74)
        grid = QGridLayout()
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setHorizontalSpacing(22)

        self.readouts = {}
        for column, (key, caption) in enumerate((
            ("dt", "Delta time"), ("f", "1 / Delta t"),
            ("va", "V @ A"), ("vb", "V @ B"), ("dv", "Delta V"),
        )):
            grid.addWidget(SectionLabel(caption), 0, column)
            value = QLabel("--")
            value.setObjectName("readout")
            value.setFont(theme.mono_font(13, bold=True))
            grid.addWidget(value, 1, column)
            self.readouts[key] = value

        grid.setColumnStretch(5, 1)
        frame.body.setContentsMargins(6, 4, 6, 4)
        frame.body.addLayout(grid)
        return frame

    # -- repository --------------------------------------------------------

    def _build_repository(self):
        panel = Panel("Data repository")
        panel.setMinimumWidth(280)

        self.session_label = QLabel("Session: LAB_ANALYSIS")
        self.session_label.setFont(theme.mono_font(8))
        self.session_label.setStyleSheet(f"color:{theme.OUTLINE};")
        panel.body.addWidget(self.session_label)

        capture = QPushButton("CAPTURE SCOPE SCREEN")
        capture.setObjectName("accent")
        capture.setFont(theme.mono_font(9, bold=True))
        capture.setMinimumHeight(30)
        capture.setToolTip(
            "Transfers the scope's own screen bitmap.\n"
            "About 170 kB, so roughly 30 s at 57600 baud."
        )
        capture.clicked.connect(self._request_capture)
        panel.body.addWidget(capture)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        panel.body.addWidget(self.progress)

        panel.body.addWidget(SectionLabel("Recent captures"))
        self.captures = QListWidget()
        self.captures.setIconSize(self.captures.iconSize().scaled(
            96, 72, Qt.AspectRatioMode.KeepAspectRatio))
        self.captures.itemDoubleClicked.connect(self._open_capture)
        panel.body.addWidget(self.captures, 2)

        panel.body.addWidget(SectionLabel("Exported data"))
        self.exports = QListWidget()
        panel.body.addWidget(self.exports, 1)

        buttons = QHBoxLayout()
        save_all = QPushButton("EXPORT ALL")
        save_all.setFont(theme.mono_font(9))
        save_all.clicked.connect(self._export_all)
        clear = QPushButton("CLEAR")
        clear.setFont(theme.mono_font(9))
        clear.clicked.connect(self._clear_store)
        buttons.addWidget(save_all)
        buttons.addWidget(clear)
        panel.body.addLayout(buttons)

        return panel

    # -- behaviour ---------------------------------------------------------

    def _on_mode(self, mode):
        self.stack.setCurrentIndex(1 if mode == "FFT" else 0)
        self.window.setVisible(mode == "FFT")
        self.cursor_toggle.setEnabled(mode == "TIME")
        if mode == "FFT":
            self._recompute_fft()

    def _on_cursors(self, on):
        self.plot.set_cursors_visible(on)
        if not on:
            for value in self.readouts.values():
                value.setText("--")
        else:
            self._update_cursor_readout()

    def update_trace(self, source, times, values):
        """Mirror a streamed frame into this view."""
        self.plot.update_analog(source, times, values)
        if self.mode.value() == "FFT":
            self._recompute_fft()
        elif self.cursor_toggle.isChecked():
            self._update_cursor_readout()

    def _recompute_fft(self):
        source = self.cursor_source.currentData()
        data = self.plot.trace(source)
        if data is None:
            return
        freqs, db = spectrum(data[0], data[1], self.window.currentData() or "hann")
        if freqs.size:
            self.fft_curve.setData(freqs, db)

    def _update_cursor_readout(self):
        if not self.cursor_toggle.isChecked():
            return

        delta_t, one_over = self.plot.cursor_readout()
        source = self.cursor_source.currentData()
        a = self.plot.cursor_a.value()
        b = self.plot.cursor_b.value()
        va = self.plot.value_at(source, a)
        vb = self.plot.value_at(source, b)

        self.readouts["dt"].setText(format_si(delta_t, "s"))
        self.readouts["f"].setText(format_si(one_over, "Hz") if np.isfinite(one_over) else "--")
        self.readouts["va"].setText(format_si(va, "V"))
        self.readouts["vb"].setText(format_si(vb, "V"))
        self.readouts["dv"].setText(format_si(vb - va, "V"))

    # -- captures and exports ---------------------------------------------

    def _request_capture(self):
        self.progress.setVisible(True)
        self.progress.setFormat("requesting bitmap...")
        self.status_message.emit(
            "Screen bitmap requested -- about 30 s at 57600 baud")
        self.instrument.capture_screen()

    def on_bulk_progress(self, received, elapsed):
        self.progress.setVisible(True)
        rate = received / elapsed if elapsed > 0 else 0
        self.progress.setFormat(f"{received // 1024} kB  ({rate / 1024:.1f} kB/s)")

    def on_capture_finished(self, payload):
        self.progress.setVisible(False)
        try:
            self.store.add_capture(payload)
        except Exception as exc:                       # noqa: BLE001 - report to user
            QMessageBox.warning(self, "Capture failed",
                                f"The scope returned data that is not a readable "
                                f"bitmap:\n\n{exc}")
            return
        self.status_message.emit(f"Screen bitmap stored ({len(payload) // 1024} kB)")

    def on_capture_failed(self):
        self.progress.setVisible(False)

    def _refresh_repository(self):
        self.captures.clear()
        for capture in self.store.captures:
            item = QListWidgetItem(
                f"{capture.name}\n{capture.stamp}   {capture.size // 1024} kB")
            if capture.thumbnail:
                item.setIcon(capture.thumbnail)
            item.setData(Qt.ItemDataRole.UserRole, capture)
            item.setFont(theme.mono_font(8))
            self.captures.addItem(item)

        self.exports.clear()
        for export in self.store.exports:
            item = QListWidgetItem(f"{export.name}\n{export.detail}")
            item.setFont(theme.mono_font(8))
            self.exports.addItem(item)

    def _open_capture(self, item):
        capture = item.data(Qt.ItemDataRole.UserRole)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save capture", capture.name, "Images (*.png *.bmp *.jpg)")
        if path:
            capture.image.save(path)
            self.status_message.emit(f"Saved {path}")

    def _export_csv(self):
        source = self.cursor_source.currentData()
        data = self.plot.trace(source)
        if data is None:
            QMessageBox.information(self, "Nothing to export",
                                    "No trace has been acquired yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export waveform",
            f"{channel_label(source).lower()}_{time.strftime('%H%M%S')}.csv",
            "CSV (*.csv)")
        if not path:
            return

        times, volts = data
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", "volts"])
            writer.writerows(zip(times.tolist(), volts.tolist()))

        self.store.add_export(Path(path).name, f"{len(times)} pts")
        self.status_message.emit(f"Exported {len(times)} points to {path}")

    def _export_all(self):
        directory = QFileDialog.getExistingDirectory(self, "Export everything to folder")
        if not directory:
            return
        written = self.store.export_all(Path(directory))
        self.status_message.emit(f"Wrote {written} file(s) to {directory}")

    def _clear_store(self):
        confirm = QMessageBox.question(
            self, "Clear stored data",
            "Discard all captures and export entries held in memory?")
        if confirm == QMessageBox.StandardButton.Yes:
            self.store.clear()
