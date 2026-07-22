"""
Waveform display.

Traces are reconstructed locally from the raw sample block, which is the
whole point of the architecture: cursors, zoom, measurements and FFT all
operate on real voltages and cost no extra link bandwidth.
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QWidget, QVBoxLayout

from . import theme
from .protocol import H_DIVISIONS

pg.setConfigOptions(antialias=True, background=theme.SURFACE_LOWEST,
                    foreground=theme.OUTLINE)

PERSISTENCE_DEPTH = 12


class WaveformPlot(QWidget):
    """Analog traces, an optional digital lane, and two measurement cursors."""

    cursors_moved = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._layout_widget = pg.GraphicsLayoutWidget()
        self._layout_widget.setBackground(theme.SURFACE_LOWEST)
        layout.addWidget(self._layout_widget)

        # -- analog plot ---------------------------------------------------
        self.analog = self._layout_widget.addPlot(row=0, col=0)
        self.analog.setLabel("left", "Volts", units="V")
        self.analog.setLabel("bottom", "Time", units="s")
        self.analog.setMenuEnabled(False)
        self.analog.showGrid(x=True, y=True, alpha=0.35)

        # -- digital lane, x-linked, hidden until a pod is selected --------
        self.digital = self._layout_widget.addPlot(row=1, col=0)
        self.digital.setMenuEnabled(False)
        self.digital.setXLink(self.analog)
        self.digital.hideAxis("bottom")
        self.digital.getAxis("left").setWidth(self.analog.getAxis("left").width())
        self.digital.setMouseEnabled(x=True, y=False)
        self.digital.setLabel("left", "Digital")
        self._layout_widget.ci.layout.setRowStretchFactor(0, 3)
        self._layout_widget.ci.layout.setRowStretchFactor(1, 1)

        # Hiding a PlotItem leaves its row occupying space in the graphics
        # layout, so the lane is detached entirely until a pod is displayed.
        self._layout_widget.ci.removeItem(self.digital)
        self._digital_attached = False

        self._curves = {}
        self._pod_curves = {}
        self._ghosts = {}
        self._persistence = False
        self._trace_width = 1.5
        self._last = {}

        self._add_watermark()
        self._add_cursors()

    # -- decoration --------------------------------------------------------

    def _add_watermark(self):
        text = pg.TextItem("AGILENT 54622D // REALTIME_ACQ", anchor=(1, 1),
                           color=QColor(theme.OUTLINE_VARIANT))
        text.setFont(QFont(theme.mono_font(7)))
        self.analog.addItem(text, ignoreBounds=True)
        self._watermark = text
        self.analog.getViewBox().sigRangeChanged.connect(self._place_watermark)

    def _place_watermark(self):
        view = self.analog.getViewBox().viewRange()
        self._watermark.setPos(view[0][1], view[1][0])

    def _add_cursors(self):
        self._cursors_placed = False
        pen = pg.mkPen(theme.PRIMARY_CONTAINER, width=1,
                       style=Qt.PenStyle.DashLine)
        hover = pg.mkPen(theme.PRIMARY, width=2)

        self.cursor_a = pg.InfiniteLine(angle=90, movable=True, pen=pen,
                                        hoverPen=hover, label="A",
                                        labelOpts={"position": 0.95,
                                                   "color": theme.PRIMARY_CONTAINER})
        self.cursor_b = pg.InfiniteLine(angle=90, movable=True, pen=pen,
                                        hoverPen=hover, label="B",
                                        labelOpts={"position": 0.95,
                                                   "color": theme.PRIMARY_CONTAINER})

        for cursor in (self.cursor_a, self.cursor_b):
            cursor.setVisible(False)
            cursor.sigPositionChanged.connect(self._emit_cursors)
            self.analog.addItem(cursor, ignoreBounds=True)

    def _emit_cursors(self):
        self.cursors_moved.emit(self.cursor_a.value(), self.cursor_b.value())

    # -- configuration -----------------------------------------------------

    def set_cursors_visible(self, on):
        if on:
            self._park_cursors()
        self.cursor_a.setVisible(on)
        self.cursor_b.setVisible(on)
        if on:
            self._emit_cursors()

    def _park_cursors(self):
        """
        Place the cursors inside the acquired trace.

        Anchoring to the data extent rather than the current view keeps them
        on screen even when the view has not yet auto-ranged to a new
        acquisition, which would otherwise strand them off the plot.
        """
        low, high = self._data_extent()
        if low is None:
            return

        width = high - low
        # Both lines default to t=0, which is legitimately inside most
        # captures, so an in-range test alone would leave them coincident and
        # report a zero delta. Spread them on first use instead.
        spread = not self._cursors_placed or self.cursor_a.value() == self.cursor_b.value()

        for cursor, fraction in ((self.cursor_a, 0.35), (self.cursor_b, 0.65)):
            if spread or not (low <= cursor.value() <= high):
                cursor.setValue(low + width * fraction)

        self._cursors_placed = True

    def _data_extent(self):
        """(first, last) time across every drawn trace, or (None, None)."""
        starts, ends = [], []
        for times, _ in self._last.values():
            if len(times):
                starts.append(float(times[0]))
                ends.append(float(times[-1]))

        if not starts:
            span = self.analog.getViewBox().viewRange()[0]
            return (span[0], span[1]) if span[1] > span[0] else (None, None)
        return min(starts), max(ends)

    def set_grid_intensity(self, percent):
        self.analog.showGrid(x=True, y=True, alpha=max(0.02, percent / 100.0))
        self.digital.showGrid(x=True, alpha=max(0.02, percent / 100.0))

    def set_trace_width(self, width):
        self._trace_width = width
        for source, curve in self._curves.items():
            curve.setPen(pg.mkPen(theme.TRACE_COLOURS.get(source, theme.SECONDARY),
                                  width=width))

    def set_persistence(self, on):
        self._persistence = on
        if not on:
            self.clear_persistence()

    def clear_persistence(self):
        for ghosts in self._ghosts.values():
            for ghost in ghosts:
                self.analog.removeItem(ghost)
        self._ghosts.clear()

    def set_source_visible(self, source, visible):
        if source in self._curves:
            self._curves[source].setVisible(visible)
        for curve in self._pod_curves.get(source, []):
            curve.setVisible(visible)
        if source.startswith("POD"):
            self._refresh_digital_visibility()

    # -- data --------------------------------------------------------------

    def update_analog(self, source, times, volts):
        colour = theme.TRACE_COLOURS.get(source, theme.SECONDARY)

        if self._persistence and source in self._curves:
            self._push_ghost(source, colour)

        if source not in self._curves:
            self._curves[source] = self.analog.plot(
                pen=pg.mkPen(colour, width=self._trace_width),
                connect="finite",  # NaN gaps break the line instead of spiking
            )

        self._curves[source].setData(times, volts)
        self._last[source] = (times, volts)

    def _push_ghost(self, source, colour):
        times, volts = self._last.get(source, (None, None))
        if times is None:
            return

        ghosts = self._ghosts.setdefault(source, [])
        faded = QColor(colour)
        faded.setAlpha(45)
        ghost = self.analog.plot(times, volts,
                                 pen=pg.mkPen(faded, width=1), connect="finite")
        ghosts.append(ghost)

        while len(ghosts) > PERSISTENCE_DEPTH:
            self.analog.removeItem(ghosts.pop(0))

        # Fade the tail so older sweeps recede.
        for index, item in enumerate(ghosts):
            alpha = int(20 + 45 * (index + 1) / len(ghosts))
            shade = QColor(colour)
            shade.setAlpha(alpha)
            item.setPen(pg.mkPen(shade, width=1))

    def update_pod(self, source, times, bits):
        """Draw eight logic traces stacked in the digital lane."""
        curves = self._pod_curves.setdefault(source, [])
        base = 0 if source == "POD1" else 9
        colour = theme.TRACE_COLOURS.get(source, theme.PRIMARY_CONTAINER)

        while len(curves) < bits.shape[0]:
            curves.append(self.digital.plot(pen=pg.mkPen(colour, width=1.2)))

        for index, curve in enumerate(curves):
            # 0.72 keeps a visible gap between adjacent logic lanes.
            curve.setData(times, bits[index] * 0.72 + base + index)

        self.digital.setYRange(base - 0.4, base + bits.shape[0] + 0.2)
        self._refresh_digital_visibility()

    def _refresh_digital_visibility(self):
        wanted = any(
            curves and curves[0].isVisible()
            for curves in self._pod_curves.values()
        )
        if wanted == self._digital_attached:
            return

        if wanted:
            self._layout_widget.ci.addItem(self.digital, row=1, col=0)
            self._layout_widget.ci.layout.setRowStretchFactor(0, 3)
            self._layout_widget.ci.layout.setRowStretchFactor(1, 1)
        else:
            self._layout_widget.ci.removeItem(self.digital)

        self._digital_attached = wanted

    def clear(self):
        for curve in self._curves.values():
            curve.clear()
        for curves in self._pod_curves.values():
            for curve in curves:
                curve.clear()
        self._last.clear()
        self.clear_persistence()

    # -- helpers -----------------------------------------------------------

    def trace(self, source):
        """(times, volts) most recently drawn for a source, or None."""
        return self._last.get(source)

    def autoscale_to(self, seconds_per_div, volts_per_div, offset=0.0):
        """Frame the view to the instrument's own grid."""
        half_t = seconds_per_div * H_DIVISIONS / 2
        self.analog.setXRange(-half_t, half_t, padding=0.02)
        self.analog.setYRange(offset - volts_per_div * 4,
                              offset + volts_per_div * 4, padding=0.02)

    def cursor_readout(self):
        """(delta_t, one_over_delta_t) for the current cursor positions."""
        delta = abs(self.cursor_b.value() - self.cursor_a.value())
        return delta, (1.0 / delta if delta > 0 else float("inf"))

    def value_at(self, source, t):
        """Interpolate a trace at time t, for cursor voltage readouts."""
        data = self._last.get(source)
        if data is None:
            return float("nan")
        times, volts = data
        if len(times) == 0:
            return float("nan")
        return float(np.interp(t, times, volts))
