"""
Measurements & Automation view.

The statistics table accumulates across acquisitions. Because every value is
computed locally from the sample block, adding a row costs no additional link
bandwidth -- which is what makes a table this dense practical at 5.7 kB/s.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QSplitter,
    QDoubleSpinBox, QAbstractItemView,
)

from .. import theme
from ..measure import MEASUREMENTS, Statistic, gate
from ..plot import WaveformPlot
from ..protocol import (
    ANALOG_CHANNELS, SECONDS_PER_DIV, VOLTS_PER_DIV, channel_label, format_si,
)
from ..widgets import Panel, Dial, SectionLabel, labelled

COLUMNS = ("REF", "MEASUREMENT", "SOURCE", "CURRENT", "MEAN", "STD DEV", "MIN", "MAX")


class AutomationView(QWidget):
    """Gated measurements with running statistics."""

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self._stats = []
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_plot())
        splitter.addWidget(self._build_lower())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([420, 330])
        root.addWidget(splitter)

    def _build_plot(self):
        panel = Panel(flat=True)
        panel.body.setContentsMargins(8, 8, 8, 8)
        self.plot = WaveformPlot()
        panel.body.addWidget(self.plot)
        return panel

    def _build_lower(self):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        row.addWidget(self._build_config(), 0)
        row.addWidget(self._build_table(), 1)
        row.addWidget(self._build_knobs(), 0)
        return holder

    # -- config ------------------------------------------------------------

    def _build_config(self):
        panel = Panel("Config")
        panel.setFixedWidth(232)

        self.source = QComboBox()
        for src in ANALOG_CHANNELS:
            self.source.addItem(f"{channel_label(src)} (Analog)", src)
        panel.body.addLayout(labelled("Measurement source", self.source))

        self.kind = QComboBox()
        for name in MEASUREMENTS:
            self.kind.addItem(name, name)
        panel.body.addLayout(labelled("Measurement", self.kind))

        add = QPushButton("+  ADD MEASUREMENT")
        add.setObjectName("accent")
        add.setFont(theme.mono_font(9, bold=True))
        add.clicked.connect(self._add_measurement)
        panel.body.addWidget(add)

        panel.body.addWidget(SectionLabel("Gating region"))
        gating = QHBoxLayout()
        self.gate_start = QDoubleSpinBox()
        self.gate_end = QDoubleSpinBox()
        for box in (self.gate_start, self.gate_end):
            box.setRange(-100.0, 100.0)
            box.setDecimals(6)
            box.setSingleStep(1e-3)
            box.setSuffix(" s")
        gating.addLayout(labelled("Start", self.gate_start))
        gating.addLayout(labelled("End", self.gate_end))
        panel.body.addLayout(gating)

        self.gate_enabled = QCheckBox("Restrict to gating region")
        panel.body.addWidget(self.gate_enabled)

        panel.body.addWidget(SectionLabel("Statistics"))
        self.show_mean = QCheckBox("Mean / std dev")
        self.show_mean.setChecked(True)
        self.show_mean.toggled.connect(self._apply_column_visibility)
        self.show_extremes = QCheckBox("Min / max values")
        self.show_extremes.setChecked(True)
        self.show_extremes.toggled.connect(self._apply_column_visibility)
        panel.body.addWidget(self.show_mean)
        panel.body.addWidget(self.show_extremes)

        clear = QPushButton("CLEAR ALL STATS")
        clear.setFont(theme.mono_font(9))
        clear.clicked.connect(self._clear_stats)
        panel.body.addWidget(clear)

        remove = QPushButton("REMOVE SELECTED")
        remove.setFont(theme.mono_font(9))
        remove.clicked.connect(self._remove_selected)
        panel.body.addWidget(remove)

        panel.body.addStretch(1)
        return panel

    def _build_table(self):
        panel = Panel("Measurement results")

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFont(theme.mono_font(9))

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        panel.body.addWidget(self.table)
        return panel

    def _build_knobs(self):
        panel = Panel("Scale")
        panel.setFixedWidth(140)

        self.time_dial = Dial(SECONDS_PER_DIV, "s", "horizontal",
                              index=SECONDS_PER_DIV.index(20e-3))
        self.time_dial.changed.connect(
            lambda v: None if self._syncing else self.instrument.set_timebase_scale(v))

        self.volt_dial = Dial(VOLTS_PER_DIV, "V", "vertical",
                              index=VOLTS_PER_DIV.index(0.5))
        self.volt_dial.changed.connect(
            lambda v: None if self._syncing
            else self.instrument.set_scale(self.source.currentData(), v))

        for dial in (self.time_dial, self.volt_dial):
            wrap = QHBoxLayout()
            wrap.addStretch(1)
            wrap.addWidget(dial)
            wrap.addStretch(1)
            panel.body.addLayout(wrap)

        panel.body.addStretch(1)
        return panel

    # -- measurements ------------------------------------------------------

    def _add_measurement(self):
        name = self.kind.currentData()
        source = self.source.currentData()
        _, unit = MEASUREMENTS[name]

        if any(s.name == name and s.source == source for s in self._stats):
            return

        self._stats.append(Statistic(name=name, source=source, unit=unit))
        self._rebuild_rows()

    def _remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            if 0 <= row < len(self._stats):
                self._stats.pop(row)
        self._rebuild_rows()

    def _clear_stats(self):
        for stat in self._stats:
            stat.reset()
        self._refresh_values()

    def _rebuild_rows(self):
        self.table.setRowCount(len(self._stats))
        for row, stat in enumerate(self._stats):
            self._set_cell(row, 0, f"M{row + 1}", theme.PRIMARY_CONTAINER)
            self._set_cell(row, 1, stat.name, theme.TERTIARY)
            self._set_cell(row, 2, channel_label(stat.source))
        self._refresh_values()
        self._apply_column_visibility()

    def _set_cell(self, row, column, text, colour=None):
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, column, item)
        item.setText(text)
        if colour:
            item.setForeground(QColor(colour))

    def _apply_column_visibility(self):
        self.table.setColumnHidden(4, not self.show_mean.isChecked())
        self.table.setColumnHidden(5, not self.show_mean.isChecked())
        self.table.setColumnHidden(6, not self.show_extremes.isChecked())
        self.table.setColumnHidden(7, not self.show_extremes.isChecked())

    def update_trace(self, source, times, values):
        """Recompute every statistic bound to this source."""
        self.plot.update_analog(source, times, values)

        if self.gate_enabled.isChecked():
            times, values = gate(times, values,
                                 self.gate_start.value(), self.gate_end.value())

        for stat in self._stats:
            if stat.source != source:
                continue
            function, _ = MEASUREMENTS[stat.name]
            try:
                stat.add(function(times, values))
            except (ValueError, FloatingPointError):
                stat.add(float("nan"))

        self._refresh_values()

    def _refresh_values(self):
        for row, stat in enumerate(self._stats):
            unit = stat.unit
            self._set_cell(row, 3, format_si(stat.current, unit), theme.ON_SURFACE)
            self._set_cell(row, 4, format_si(stat.mean, unit))
            self._set_cell(row, 5, format_si(stat.std_dev, unit))
            self._set_cell(row, 6, format_si(stat.minimum, unit)
                           if stat.count else "--")
            self._set_cell(row, 7, format_si(stat.maximum, unit)
                           if stat.count else "--")

    def sync_scales(self):
        self._syncing = True
        try:
            self.time_dial.set_value(self.instrument.timebase.scale)
            channel = self.instrument.channels.get(self.source.currentData())
            if channel:
                self.volt_dial.set_value(channel.scale)
        finally:
            self._syncing = False
