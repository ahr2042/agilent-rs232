"""
High-level instrument model.

Holds the desired state of the scope and turns UI actions into SCPI, keeping
the views free of protocol detail. Every write goes through SerialLink, so
nothing here blocks.
"""

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from .protocol import ANALOG_CHANNELS, DIGITAL_PODS, AcquireType, WaveFormat
from .transport import Kind, Priority, SerialLink


@dataclass
class ChannelState:
    source: str
    enabled: bool = True
    scale: float = 0.5           # volts/div
    offset: float = 0.0
    coupling: str = "DC"         # the 546xx is 1 MOhm only: AC or DC
    bandwidth_limit: bool = False
    probe: float = 10.0
    inverted: bool = False


@dataclass
class PodState:
    source: str
    enabled: bool = False
    threshold: float = 1.4       # TTL


@dataclass
class TriggerState:
    source: str = "CHAN1"
    level: float = 0.0
    slope: str = "POSitive"
    sweep: str = "AUTO"
    holdoff: float = 200e-9
    noise_reject: bool = False


@dataclass
class TimebaseState:
    scale: float = 20e-3         # seconds/div
    position: float = 0.0
    mode: str = "MAIN"


@dataclass
class AcquireState:
    type: AcquireType = AcquireType.NORMAL
    count: int = 8
    points: int = 1000
    format: WaveFormat = WaveFormat.BYTE


class Instrument(QObject):
    """Facade over SerialLink holding the scope's settings."""

    state_changed = pyqtSignal()
    run_state_changed = pyqtSignal(bool)
    identity_changed = pyqtSignal(str)

    def __init__(self, link: SerialLink, parent=None):
        super().__init__(parent)
        self.link = link

        self.channels = {s: ChannelState(s) for s in ANALOG_CHANNELS}
        self.channels["CHAN2"].scale = 1.0
        self.channels["CHAN2"].coupling = "AC"

        self.pods = {s: PodState(s) for s in DIGITAL_PODS}
        self.trigger = TriggerState()
        self.timebase = TimebaseState()
        self.acquire = AcquireState()

        self.running = False
        self.identity = ""

        link.link_changed.connect(self._on_link_changed)

    # -- lifecycle ---------------------------------------------------------

    def _on_link_changed(self, connected, description):
        if connected:
            self.identity = description
            self.identity_changed.emit(description)
        else:
            self.running = False
            self.run_state_changed.emit(False)

    def active_sources(self):
        """Sources currently selected for transfer, analog first."""
        sources = [s for s, c in self.channels.items() if c.enabled]
        sources += [s for s, p in self.pods.items() if p.enabled]
        return sources

    def _push_sources(self):
        self.link.set_sources(self.active_sources())

    # -- acquisition -------------------------------------------------------

    def run(self):
        self.link.submit(b":RUN\n", Kind.WRITE, Priority.URGENT)
        self.running = True
        self._push_sources()
        self.link.set_streaming(True)
        self.run_state_changed.emit(True)

    def stop(self):
        # URGENT so a queued backlog cannot delay a stop request.
        self.link.set_streaming(False)
        self.link.submit(b":STOP\n", Kind.WRITE, Priority.URGENT)
        self.running = False
        self.run_state_changed.emit(False)

    def single(self):
        self.link.set_streaming(False)
        self.link.submit(b":SINGle\n", Kind.WRITE, Priority.URGENT)
        self.running = False
        self.run_state_changed.emit(False)

    def autoscale(self):
        self.link.submit(b":AUToscale\n", Kind.WRITE, Priority.URGENT, timeout=10.0)
        self.link.invalidate_preamble()

    def set_acquire_type(self, acq_type: AcquireType):
        self.acquire.type = acq_type
        self.link.submit(f":ACQuire:TYPE {acq_type.value}\n", Kind.WRITE)
        if acq_type is AcquireType.AVERAGE:
            self.link.submit(f":ACQuire:COUNt {self.acquire.count}\n", Kind.WRITE)
        # Averaging genuinely accumulates sub-LSB bits, so WORD earns its
        # doubled transfer size there and nowhere else.
        self.set_format(WaveFormat.WORD if acq_type is AcquireType.AVERAGE
                        else WaveFormat.BYTE)
        self.state_changed.emit()

    def set_points(self, points: int):
        self.acquire.points = points
        self.link.set_capture(points, self.acquire.format)
        self.state_changed.emit()

    def set_format(self, fmt: WaveFormat):
        self.acquire.format = fmt
        self.link.set_capture(self.acquire.points, fmt)
        self.state_changed.emit()

    # -- vertical ----------------------------------------------------------

    def set_channel_enabled(self, source, enabled):
        self.channels[source].enabled = enabled
        self.link.submit(f":CHANnel{source[-1]}:DISPlay {'ON' if enabled else 'OFF'}\n",
                         Kind.WRITE)
        self._push_sources()
        self.state_changed.emit()

    def set_scale(self, source, volts_per_div):
        self.channels[source].scale = volts_per_div
        self.link.submit(f":CHANnel{source[-1]}:SCALe {volts_per_div:.6G}\n", Kind.WRITE)
        self.link.invalidate_preamble()
        self.state_changed.emit()

    def set_offset(self, source, volts):
        self.channels[source].offset = volts
        self.link.submit(f":CHANnel{source[-1]}:OFFSet {volts:.6G}\n", Kind.WRITE)
        self.link.invalidate_preamble()
        self.state_changed.emit()

    def set_coupling(self, source, coupling):
        self.channels[source].coupling = coupling
        self.link.submit(f":CHANnel{source[-1]}:COUPling {coupling}\n", Kind.WRITE)
        self.state_changed.emit()

    def set_bandwidth_limit(self, source, limited):
        self.channels[source].bandwidth_limit = limited
        self.link.submit(f":CHANnel{source[-1]}:BWLimit {'ON' if limited else 'OFF'}\n",
                         Kind.WRITE)
        self.state_changed.emit()

    def set_probe(self, source, factor):
        self.channels[source].probe = factor
        self.link.submit(f":CHANnel{source[-1]}:PROBe {factor:.6G}\n", Kind.WRITE)
        self.link.invalidate_preamble()
        self.state_changed.emit()

    def set_invert(self, source, inverted):
        self.channels[source].inverted = inverted
        self.link.submit(f":CHANnel{source[-1]}:INVert {'ON' if inverted else 'OFF'}\n",
                         Kind.WRITE)
        self.state_changed.emit()

    # -- digital pods ------------------------------------------------------

    def set_pod_enabled(self, source, enabled):
        self.pods[source].enabled = enabled
        self.link.submit(f":POD{source[-1]}:DISPlay {'ON' if enabled else 'OFF'}\n",
                         Kind.WRITE)
        self._push_sources()
        self.state_changed.emit()

    def set_pod_threshold(self, source, volts):
        self.pods[source].threshold = volts
        self.link.submit(f":POD{source[-1]}:THReshold {volts:.4G}\n", Kind.WRITE)
        self.state_changed.emit()

    # -- horizontal --------------------------------------------------------

    def set_timebase_scale(self, seconds_per_div):
        self.timebase.scale = seconds_per_div
        self.link.submit(f":TIMebase:SCALe {seconds_per_div:.6G}\n", Kind.WRITE)
        self.link.invalidate_preamble()
        self.state_changed.emit()

    def set_timebase_position(self, seconds):
        self.timebase.position = seconds
        self.link.submit(f":TIMebase:POSition {seconds:.6G}\n", Kind.WRITE)
        self.link.invalidate_preamble()
        self.state_changed.emit()

    # -- trigger -----------------------------------------------------------

    def set_trigger_source(self, source):
        self.trigger.source = source
        self.link.submit(f":TRIGger:EDGE:SOURce {source}\n", Kind.WRITE)
        self.state_changed.emit()

    def set_trigger_level(self, volts):
        self.trigger.level = volts
        self.link.submit(f":TRIGger:EDGE:LEVel {volts:.6G}\n", Kind.WRITE)
        self.state_changed.emit()

    def set_trigger_slope(self, slope):
        self.trigger.slope = slope
        self.link.submit(f":TRIGger:EDGE:SLOPe {slope}\n", Kind.WRITE)
        self.state_changed.emit()

    def set_trigger_sweep(self, sweep):
        self.trigger.sweep = sweep
        self.link.submit(f":TRIGger:SWEep {sweep}\n", Kind.WRITE)
        self.state_changed.emit()

    def set_trigger_holdoff(self, seconds):
        self.trigger.holdoff = seconds
        self.link.submit(f":TRIGger:HOLDoff {seconds:.6G}\n", Kind.WRITE)
        self.state_changed.emit()

    def set_noise_reject(self, on):
        self.trigger.noise_reject = on
        self.link.submit(f":TRIGger:NREJect {'ON' if on else 'OFF'}\n", Kind.WRITE)
        self.state_changed.emit()

    # -- utility -----------------------------------------------------------

    def reset(self):
        self.link.submit(b"*RST\n", Kind.WRITE, Priority.URGENT, timeout=10.0)
        self.link.invalidate_preamble()

    def clear_status(self):
        self.link.submit(b"*CLS\n", Kind.WRITE, Priority.URGENT)

    def query_errors(self):
        self.link.submit(b":SYSTem:ERRor?\n", Kind.QUERY, Priority.QUERY, tag="sys:error")

    def capture_screen(self):
        """
        Request the screen bitmap.

        BULK priority and a long timeout: this is roughly 170 kB, about 30 s
        at 57600 baud. It is a deliberate one-shot action, never a refresh
        loop -- see the streaming path for live display. solo=True pauses
        streaming and clears the wire so the transfer cannot pick up a
        waveform block left over from the acquisition loop.
        """
        self.link.submit(b":DISPlay:DATA? BMP\n", Kind.BLOCK, Priority.BULK,
                         timeout=240.0, tag="display:bitmap", solo=True)

    def send_raw(self, text: str, expect_response=None):
        """Send a hand-typed command from the console."""
        text = text.strip()
        if not text:
            return
        if expect_response is None:
            expect_response = "?" in text
        self.link.submit(
            (text + "\n").encode("ascii", "replace"),
            Kind.QUERY if expect_response else Kind.WRITE,
            Priority.CONTROL, timeout=5.0, tag=text,
        )
