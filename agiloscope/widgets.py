"""Reusable widgets shared by the four views."""

from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QButtonGroup, QSizePolicy,
)

from . import theme


class Panel(QFrame):
    """Titled container with the design's header bar."""

    def __init__(self, title=None, flat=False, parent=None):
        super().__init__(parent)
        self.setObjectName("panelFlat" if flat else "panel")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        if title:
            header = QLabel(title.upper())
            header.setObjectName("panelTitle")
            header.setFont(theme.mono_font(9, bold=True))
            self._outer.addWidget(header)
            self._header = header
        else:
            self._header = None

        self.body = QVBoxLayout()
        self.body.setContentsMargins(10, 10, 10, 10)
        self.body.setSpacing(8)
        self._outer.addLayout(self.body)

    def set_title(self, text):
        if self._header:
            self._header.setText(text.upper())

    def add_header_widget(self, widget):
        """Place a control on the right-hand end of the title bar."""
        if not self._header:
            return
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._outer.removeWidget(self._header)
        row.addWidget(self._header, 1)
        widget.setParent(self)
        row.addWidget(widget, 0)
        holder = QWidget()
        holder.setLayout(row)
        holder.setStyleSheet(f"background:{theme.SURFACE_HIGH};")
        self._outer.insertWidget(0, holder)


class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")
        self.setFont(theme.mono_font(8))


class Segmented(QWidget):
    """Exclusive segmented button row, e.g. COUPLING  [AC][DC]."""

    changed = pyqtSignal(str)

    def __init__(self, options, current=None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}

        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setFont(theme.mono_font(9))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(button)
            row.addWidget(button)
            self._buttons[value] = button
            button.clicked.connect(lambda _, v=value: self.changed.emit(v))

        if current in self._buttons:
            self._buttons[current].setChecked(True)
        elif options:
            self._buttons[options[0][0]].setChecked(True)

    def value(self):
        for value, button in self._buttons.items():
            if button.isChecked():
                return value
        return None

    def set_value(self, value):
        """Set without emitting `changed`."""
        button = self._buttons.get(value)
        if button:
            button.setChecked(True)


class Dial(QWidget):
    """
    Circular value selector stepping through a fixed sequence.

    Matches the SCALE / HORIZONTAL SCALE knobs in the design: drag vertically
    or scroll to step. Values come from a list so the knob snaps to the same
    1-2-5 sequence as the instrument's front panel.
    """

    changed = pyqtSignal(float)

    def __init__(self, values, unit, caption="", index=0, parent=None):
        super().__init__(parent)
        self._values = list(values)
        self._unit = unit
        self._caption = caption
        self._index = max(0, min(index, len(self._values) - 1))
        self._drag_origin = None
        self._accum = 0.0

        self.setMinimumSize(92, 92)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag vertically or scroll to change")

    # -- value -------------------------------------------------------------

    def value(self):
        return self._values[self._index]

    def set_value(self, value):
        """Snap to the nearest available step without emitting `changed`."""
        if not self._values:
            return
        nearest = min(range(len(self._values)),
                      key=lambda i: abs(self._values[i] - value))
        if nearest != self._index:
            self._index = nearest
            self.update()

    def _step(self, delta):
        new = max(0, min(self._index + delta, len(self._values) - 1))
        if new != self._index:
            self._index = new
            self.update()
            self.changed.emit(self.value())

    # -- interaction -------------------------------------------------------

    def wheelEvent(self, event):
        self._step(1 if event.angleDelta().y() < 0 else -1)
        event.accept()

    def mousePressEvent(self, event):
        self._drag_origin = event.position().y()
        self._accum = 0.0

    def mouseMoveEvent(self, event):
        if self._drag_origin is None:
            return
        self._accum += event.position().y() - self._drag_origin
        self._drag_origin = event.position().y()
        while abs(self._accum) >= 10:
            self._step(1 if self._accum > 0 else -1)
            self._accum -= 10 if self._accum > 0 else -10

    def mouseReleaseEvent(self, _event):
        self._drag_origin = None

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _event):
        from .protocol import format_si

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height() - 12)
        ring = QRectF((self.width() - side) / 2 + 5, 5, side - 10, side - 10)

        painter.setPen(QPen(QColor(theme.SURFACE_HIGHEST), 3))
        painter.drawArc(ring, 225 * 16, -270 * 16)

        span = self._index / max(1, len(self._values) - 1)
        painter.setPen(QPen(QColor(theme.PRIMARY_CONTAINER), 3))
        painter.drawArc(ring, 225 * 16, int(-270 * 16 * span))

        painter.setFont(theme.mono_font(10, bold=True))
        painter.setPen(QColor(theme.PRIMARY_CONTAINER))
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter,
                         format_si(self.value(), self._unit))

        if self._caption:
            painter.setFont(theme.mono_font(7))
            painter.setPen(QColor(theme.OUTLINE))
            painter.drawText(
                QRectF(0, self.height() - 12, self.width(), 12),
                Qt.AlignmentFlag.AlignCenter, self._caption.upper(),
            )


class StatusChip(QLabel):
    """Small pill showing a live state, e.g. RUNNING / STOPPED."""

    def __init__(self, text="", tone="neutral", parent=None):
        super().__init__(parent)
        self.setFont(theme.mono_font(9, bold=True))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(text, tone)

    def set_state(self, text, tone="neutral"):
        colours = {
            "ok": (theme.TERTIARY, "#16301a"),
            "warn": (theme.PRIMARY_CONTAINER, "#2e2408"),
            "error": (theme.ERROR, theme.ERROR_CONTAINER),
            "neutral": (theme.OUTLINE, theme.SURFACE_HIGH),
            "active": (theme.SECONDARY, "#062b33"),
        }
        fg, bg = colours.get(tone, colours["neutral"])
        self.setText(f" {text.upper()} ")
        self.setStyleSheet(
            f"color:{fg}; background:{bg}; border:1px solid {fg};"
            "border-radius:2px; padding:2px 8px; letter-spacing:1px;"
        )


class ChannelBadge(QFrame):
    """
    The per-channel readout tile above the plot, e.g.

        CH1: DC 1M
        500mV/div
    """

    toggled = pyqtSignal(str, bool)

    def __init__(self, source, colour, parent=None):
        super().__init__(parent)
        self._source = source
        self._colour = colour
        self._enabled = True

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self.setMinimumWidth(112)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)

        from .protocol import channel_label
        self._title = QLabel(f"{channel_label(source)}: --")
        self._title.setFont(theme.mono_font(8, bold=True))
        self._detail = QLabel("--")
        self._detail.setFont(theme.mono_font(10, bold=True))

        layout.addWidget(self._title)
        layout.addWidget(self._detail)
        self._restyle()

    def set_readout(self, title_suffix, detail):
        from .protocol import channel_label
        self._title.setText(f"{channel_label(self._source)}: {title_suffix}")
        self._detail.setText(detail)

    def set_enabled_state(self, on):
        self._enabled = on
        self._restyle()

    def mousePressEvent(self, _event):
        self._enabled = not self._enabled
        self._restyle()
        self.toggled.emit(self._source, self._enabled)

    def _restyle(self):
        colour = self._colour if self._enabled else theme.OUTLINE_VARIANT
        self.setStyleSheet(
            f"QFrame {{ background:{theme.SURFACE_LOW}; border:1px solid {colour};"
            f"border-left:3px solid {colour}; border-radius:3px; }}"
        )
        self._title.setStyleSheet(f"color:{colour}; border:none;")
        self._detail.setStyleSheet(
            f"color:{theme.ON_SURFACE if self._enabled else theme.OUTLINE_VARIANT};"
            "border:none;"
        )


def hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"background:{theme.OUTLINE_VARIANT}; max-height:1px;")
    return line


def labelled(text, widget, stretch=False):
    """Section label above a control."""
    box = QVBoxLayout()
    box.setSpacing(3)
    box.addWidget(SectionLabel(text))
    box.addWidget(widget)
    if stretch:
        box.addStretch(1)
    return box
