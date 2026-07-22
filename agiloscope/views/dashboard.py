"""Main Control Dashboard: live trace, vertical/horizontal/trigger controls, console."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSlider, QTabWidget, QSplitter, QCheckBox, QDoubleSpinBox,
)

from .. import theme
from ..protocol import (
    ANALOG_CHANNELS, DIGITAL_PODS, VOLTS_PER_DIV, SECONDS_PER_DIV,
    POINT_COUNTS, AcquireType, channel_label, format_si,
)
from ..console import ScpiConsole
from ..plot import WaveformPlot
from ..widgets import (
    Panel, Segmented, Dial, StatusChip, ChannelBadge, SectionLabel, labelled,
)


class DashboardView(QWidget):
    """The design's Main Control Dashboard."""

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self._selected = "CHAN1"
        self._syncing = False

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_scope_area())
        splitter.addWidget(self._build_console())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([620, 190])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_controls(), 0)

        instrument.state_changed.connect(self._sync_from_instrument)
        instrument.run_state_changed.connect(self._on_run_state)
        self._sync_from_instrument()

    # -- scope area --------------------------------------------------------

    def _build_scope_area(self):
        panel = Panel(flat=True)
        panel.body.setContentsMargins(8, 8, 8, 8)

        badges = QHBoxLayout()
        badges.setSpacing(8)

        self.badges = {}
        for source in ANALOG_CHANNELS:
            badge = ChannelBadge(source, theme.TRACE_COLOURS[source])
            badge.toggled.connect(self._on_badge_toggled)
            badges.addWidget(badge)
            self.badges[source] = badge

        for source in DIGITAL_PODS:
            badge = ChannelBadge(source, theme.TRACE_COLOURS[source])
            badge.set_enabled_state(False)
            badge.set_readout("digital", "D0-D7" if source == "POD1" else "D8-D15")
            badge.toggled.connect(self._on_badge_toggled)
            badges.addWidget(badge)
            self.badges[source] = badge

        self.timebase_badge = QLabel()
        self.timebase_badge.setFont(theme.mono_font(10, bold=True))
        self.timebase_badge.setStyleSheet(
            f"color:{theme.ON_SURFACE_VARIANT}; background:{theme.SURFACE_LOW};"
            f"border:1px solid {theme.OUTLINE_VARIANT}; border-radius:3px; padding:6px 10px;"
        )
        badges.addWidget(self.timebase_badge)
        badges.addStretch(1)

        self.rate_chip = StatusChip("-- FPS", "neutral")
        badges.addWidget(self.rate_chip)

        panel.body.addLayout(badges)

        self.plot = WaveformPlot()
        panel.body.addWidget(self.plot, 1)
        return panel

    def _build_console(self):
        panel = Panel("SCPI terminal")
        self.console = ScpiConsole()
        self.console.command_entered.connect(self.instrument.send_raw)
        panel.body.setContentsMargins(8, 8, 8, 8)
        panel.body.addWidget(self.console)
        return panel

    # -- control column ----------------------------------------------------

    def _build_controls(self):
        panel = Panel("Controls")
        panel.setFixedWidth(272)

        tabs = QTabWidget()
        tabs.addTab(self._vertical_tab(), "VERTICAL")
        tabs.addTab(self._horizontal_tab(), "HORIZ")
        tabs.addTab(self._trigger_tab(), "TRIG")
        panel.body.addWidget(tabs, 1)

        self.acq_button = QPushButton("START ACQUISITION")
        self.acq_button.setObjectName("danger")
        self.acq_button.setFont(theme.mono_font(10, bold=True))
        self.acq_button.setMinimumHeight(36)
        self.acq_button.clicked.connect(self._toggle_run)
        panel.body.addWidget(self.acq_button)

        return panel

    def _vertical_tab(self):
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.setContentsMargins(8, 10, 8, 8)
        box.setSpacing(10)

        self.channel_picker = Segmented(
            [(s, channel_label(s)) for s in ANALOG_CHANNELS + DIGITAL_PODS],
            current="CHAN1",
        )
        self.channel_picker.changed.connect(self._select_channel)
        box.addWidget(self.channel_picker)

        self.scale_dial = Dial(VOLTS_PER_DIV, "V", "scale (V/div)",
                               index=VOLTS_PER_DIV.index(0.5))
        self.scale_dial.changed.connect(
            lambda v: self._guarded(lambda: self.instrument.set_scale(self._selected, v)))
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(self.scale_dial)
        holder.addStretch(1)
        box.addLayout(holder)

        self.offset_label = QLabel("0.00 V")
        self.offset_label.setObjectName("readoutSmall")
        self.offset_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        offset_head = QHBoxLayout()
        offset_head.addWidget(SectionLabel("Offset"))
        offset_head.addStretch(1)
        offset_head.addWidget(self.offset_label)
        box.addLayout(offset_head)

        self.offset_slider = QSlider(Qt.Orientation.Horizontal)
        self.offset_slider.setRange(-400, 400)     # divisions x100
        self.offset_slider.valueChanged.connect(self._on_offset_slider)
        box.addWidget(self.offset_slider)

        self.coupling = Segmented([("DC", "DC 1M"), ("AC", "AC 1M")], current="DC")
        self.coupling.changed.connect(
            lambda v: self._guarded(lambda: self.instrument.set_coupling(self._selected, v)))
        box.addLayout(labelled("Coupling (1 MOhm input)", self.coupling))

        self.bandwidth = Segmented([("20M", "20M"), ("FULL", "FULL")], current="FULL")
        self.bandwidth.changed.connect(
            lambda v: self._guarded(
                lambda: self.instrument.set_bandwidth_limit(self._selected, v == "20M")))
        box.addLayout(labelled("Bandwidth", self.bandwidth))

        self.probe = QComboBox()
        for factor in (1, 10, 100):
            self.probe.addItem(f"{factor}:1", factor)
        self.probe.setCurrentIndex(1)
        self.probe.currentIndexChanged.connect(
            lambda: self._guarded(lambda: self.instrument.set_probe(
                self._selected, float(self.probe.currentData()))))
        box.addLayout(labelled("Probe", self.probe))

        self.invert = QCheckBox("Invert trace")
        self.invert.toggled.connect(
            lambda on: self._guarded(lambda: self.instrument.set_invert(self._selected, on)))
        box.addWidget(self.invert)

        self.pod_threshold = QDoubleSpinBox()
        self.pod_threshold.setRange(-8.0, 8.0)
        self.pod_threshold.setSingleStep(0.1)
        self.pod_threshold.setValue(1.4)
        self.pod_threshold.setSuffix(" V")
        self.pod_threshold.valueChanged.connect(
            lambda v: self._guarded(
                lambda: self.instrument.set_pod_threshold(self._selected, v)))
        self.pod_threshold_row = labelled("Pod logic threshold", self.pod_threshold)
        box.addLayout(self.pod_threshold_row)

        box.addStretch(1)
        return tab

    def _horizontal_tab(self):
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.setContentsMargins(8, 10, 8, 8)
        box.setSpacing(10)

        self.time_dial = Dial(SECONDS_PER_DIV, "s", "time/div",
                              index=SECONDS_PER_DIV.index(20e-3))
        self.time_dial.changed.connect(
            lambda v: self._guarded(lambda: self.instrument.set_timebase_scale(v)))
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(self.time_dial)
        holder.addStretch(1)
        box.addLayout(holder)

        self.position = QDoubleSpinBox()
        self.position.setRange(-10.0, 10.0)
        self.position.setDecimals(6)
        self.position.setSingleStep(0.001)
        self.position.setSuffix(" s")
        self.position.valueChanged.connect(
            lambda v: self._guarded(lambda: self.instrument.set_timebase_position(v)))
        box.addLayout(labelled("Horizontal delay", self.position))

        self.points = QComboBox()
        for count in POINT_COUNTS:
            self.points.addItem(str(count), count)
        self.points.setCurrentIndex(POINT_COUNTS.index(1000))
        self.points.currentIndexChanged.connect(
            lambda: self._guarded(
                lambda: self.instrument.set_points(int(self.points.currentData()))))
        box.addLayout(labelled("Points per acquisition", self.points))

        self.acq_type = QComboBox()
        for acq in AcquireType:
            self.acq_type.addItem(acq.name.title(), acq)
        self.acq_type.currentIndexChanged.connect(
            lambda: self._guarded(
                lambda: self.instrument.set_acquire_type(self.acq_type.currentData())))
        box.addLayout(labelled("Acquisition mode", self.acq_type))

        note = QLabel(
            "Fewer points refresh faster. BYTE format matches the 8-bit ADC; "
            "WORD is selected automatically for averaging."
        )
        note.setWordWrap(True)
        note.setFont(theme.mono_font(8))
        note.setStyleSheet(f"color:{theme.OUTLINE};")
        box.addWidget(note)

        box.addStretch(1)
        return tab

    def _trigger_tab(self):
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.setContentsMargins(8, 10, 8, 8)
        box.setSpacing(10)

        self.trig_sweep = Segmented(
            [("AUTO", "AUTO"), ("NORMal", "NORM")], current="AUTO")
        self.trig_sweep.changed.connect(
            lambda v: self._guarded(lambda: self.instrument.set_trigger_sweep(v)))
        box.addLayout(labelled("Sweep mode", self.trig_sweep))

        self.trig_source = QComboBox()
        for source in ANALOG_CHANNELS:
            self.trig_source.addItem(channel_label(source), source)
        self.trig_source.addItem("EXT", "EXTernal")
        self.trig_source.addItem("LINE", "LINE")
        self.trig_source.currentIndexChanged.connect(
            lambda: self._guarded(lambda: self.instrument.set_trigger_source(
                self.trig_source.currentData())))
        box.addLayout(labelled("Source", self.trig_source))

        self.trig_slope = Segmented(
            [("POSitive", "RISING"), ("NEGative", "FALLING")], current="POSitive")
        self.trig_slope.changed.connect(
            lambda v: self._guarded(lambda: self.instrument.set_trigger_slope(v)))
        box.addLayout(labelled("Slope", self.trig_slope))

        self.trig_level = QDoubleSpinBox()
        self.trig_level.setRange(-40.0, 40.0)
        self.trig_level.setDecimals(3)
        self.trig_level.setSingleStep(0.05)
        self.trig_level.setSuffix(" V")
        self.trig_level.valueChanged.connect(
            lambda v: self._guarded(lambda: self.instrument.set_trigger_level(v)))
        box.addLayout(labelled("Level", self.trig_level))

        self.holdoff = QDoubleSpinBox()
        self.holdoff.setRange(0.0, 10.0)
        self.holdoff.setDecimals(9)
        self.holdoff.setSingleStep(100e-9)
        self.holdoff.setValue(200e-9)
        self.holdoff.setSuffix(" s")
        self.holdoff.valueChanged.connect(
            lambda v: self._guarded(lambda: self.instrument.set_trigger_holdoff(v)))
        box.addLayout(labelled("Holdoff", self.holdoff))

        self.noise_reject = QCheckBox("Noise reject")
        self.noise_reject.toggled.connect(
            lambda on: self._guarded(lambda: self.instrument.set_noise_reject(on)))
        box.addWidget(self.noise_reject)

        box.addStretch(1)
        return tab

    # -- behaviour ---------------------------------------------------------

    def _guarded(self, action):
        """Ignore control signals raised while syncing the UI from state."""
        if not self._syncing:
            action()

    def _select_channel(self, source):
        self._selected = source
        self._sync_from_instrument()

    def _on_badge_toggled(self, source, enabled):
        if source in self.instrument.channels:
            self.instrument.set_channel_enabled(source, enabled)
        else:
            self.instrument.set_pod_enabled(source, enabled)
        self.plot.set_source_visible(source, enabled)

    def _on_offset_slider(self, value):
        if self._syncing or self._selected not in self.instrument.channels:
            return
        volts = value / 100.0 * self.instrument.channels[self._selected].scale
        self.offset_label.setText(format_si(volts, "V"))
        self.instrument.set_offset(self._selected, volts)

    def _toggle_run(self):
        if self.instrument.running:
            self.instrument.stop()
        else:
            self.instrument.run()

    def _on_run_state(self, running):
        self.acq_button.setText("STOP ACQUISITION" if running else "START ACQUISITION")
        if not running:
            self.rate_chip.set_state("-- FPS", "neutral")

    def set_rate(self, fps):
        self.rate_chip.set_state(f"{fps:.1f} FPS", "ok" if fps >= 2 else "warn")

    def _sync_from_instrument(self):
        self._syncing = True
        try:
            is_pod = self._selected in self.instrument.pods

            for widget in (self.scale_dial, self.offset_slider, self.coupling,
                           self.bandwidth, self.probe, self.invert):
                widget.setEnabled(not is_pod)
            self.pod_threshold.setEnabled(is_pod)

            if is_pod:
                pod = self.instrument.pods[self._selected]
                self.pod_threshold.setValue(pod.threshold)
            else:
                channel = self.instrument.channels[self._selected]
                self.scale_dial.set_value(channel.scale)
                self.coupling.set_value(channel.coupling)
                self.bandwidth.set_value("20M" if channel.bandwidth_limit else "FULL")
                self.invert.setChecked(channel.inverted)
                self.offset_label.setText(format_si(channel.offset, "V"))
                if channel.scale:
                    self.offset_slider.setValue(int(channel.offset / channel.scale * 100))

            for source, state in self.instrument.channels.items():
                self.badges[source].set_readout(
                    f"{state.coupling} 1M",
                    f"{format_si(state.scale, 'V')}/div",
                )
                self.badges[source].set_enabled_state(state.enabled)

            timebase = self.instrument.timebase
            self.timebase_badge.setText(
                f"M: {format_si(timebase.scale, 's')}/div   "
                f"{self.instrument.acquire.points} pts   "
                f"{self.instrument.acquire.format.scpi}"
            )
            self.time_dial.set_value(timebase.scale)
        finally:
            self._syncing = False
