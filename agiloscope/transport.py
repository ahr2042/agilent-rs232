"""
Threaded RS-232 transport.

The serial port is a single shared blocking resource: one outstanding
request at a time, and every query stalls until the scope answers. All of
that lives here, on its own thread, so the GUI thread never blocks on I/O.

Communication with the GUI is exclusively by Qt signals, which cross the
thread boundary as queued connections. Nothing outside this module may touch
the port.
"""

import itertools
import queue
import re
import time
from dataclasses import dataclass, field
from enum import IntEnum

import serial
from serial.tools import list_ports
from PyQt6.QtCore import QThread, pyqtSignal

from . import protocol
from .protocol import Preamble, WaveFormat, read_ieee_block


class Priority(IntEnum):
    """Lower value is serviced first."""

    URGENT = 0    # STOP, RUN -- must preempt a queued backlog
    CONTROL = 1   # knob turns, coupling changes
    QUERY = 2     # status polling
    BULK = 3      # screen bitmap: tens of seconds, always last


class Kind(IntEnum):
    WRITE = 0     # no response expected
    QUERY = 1     # single line response
    BLOCK = 2     # IEEE 488.2 definite-length block


@dataclass(order=True)
class Request:
    priority: int
    seq: int
    command: bytes = field(compare=False)
    kind: int = field(compare=False, default=Kind.WRITE)
    timeout: float = field(compare=False, default=2.0)
    tag: str = field(compare=False, default="")
    echo: bool = field(compare=False, default=True)
    # solo requests own the wire exclusively: streaming is paused and the
    # line is drained before and after, so a long transfer (the screen
    # bitmap) can never collide with a streamed waveform response.
    solo: bool = field(compare=False, default=False)


def available_ports():
    """[(device, description)] for every serial port the OS reports."""
    return [(p.device, p.description or "serial port") for p in list_ports.comports()]


class SerialLink(QThread):
    """
    Owns the serial port and services a priority queue of requests.

    Signals are the only way results leave this thread.
    """

    # tag, payload
    replied = pyqtSignal(str, bytes)
    # tag, human-readable reason
    failed = pyqtSignal(str, str)
    # direction ('TX'/'RX'/'INF'/'ERR'), text -- drives the SCPI console
    traffic = pyqtSignal(str, str)
    # connected, description
    link_changed = pyqtSignal(bool, str)
    # source, times, values, preamble  -- one streamed acquisition
    frame_ready = pyqtSignal(str, object, object, object)
    # measured frames per second
    rate_changed = pyqtSignal(float)
    # bytes received, elapsed seconds -- drives the bitmap progress bar
    bulk_progress = pyqtSignal(int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._port = None
        self._stopping = False

        self._settings = {"port": "/dev/ttyUSB0", "baud": 57600, "timeout": 2.0}

        # Streaming state
        self._streaming = False
        self._suspend_streaming = False
        self._sources = ["CHAN1"]
        self._points = 1000
        self._format = WaveFormat.BYTE
        self._preambles = {}
        self._active_source = None
        self._preamble_dirty = True
        self._frame_times = []

    # -- public API (called from the GUI thread) ---------------------------

    def configure(self, port: str, baud: int, timeout: float = 2.0):
        self._settings = {"port": port, "baud": baud, "timeout": timeout}

    def submit(self, command, kind=Kind.WRITE, priority=Priority.CONTROL,
               timeout=2.0, tag="", echo=True, solo=False):
        """Queue a request. Returns immediately."""
        if isinstance(command, str):
            command = command.encode("ascii")
        self._queue.put(Request(
            priority=int(priority), seq=next(self._seq), command=command,
            kind=int(kind), timeout=timeout, tag=tag or command.decode("ascii", "replace"),
            echo=echo, solo=solo,
        ))

    def set_streaming(self, on: bool):
        self._streaming = on
        self._frame_times.clear()

    def set_sources(self, sources):
        self._sources = list(sources)
        self._preamble_dirty = True

    def set_capture(self, points: int, fmt: WaveFormat):
        self._points = points
        self._format = fmt
        self._preamble_dirty = True

    def invalidate_preamble(self):
        """Call after any setting that changes waveform scaling."""
        self._preamble_dirty = True

    def shutdown(self):
        self._stopping = True
        self.wait(3000)

    # -- thread body -------------------------------------------------------

    def run(self):
        if not self._open():
            return

        while not self._stopping:
            try:
                request = self._queue.get(timeout=0.01)
            except queue.Empty:
                if self._streaming and not self._suspend_streaming:
                    self._stream_once()
                continue
            self._service(request)

        self._close()

    # -- connection --------------------------------------------------------

    def _open(self) -> bool:
        s = self._settings
        try:
            # DTR/DSR hardware handshaking is required by the 546xx series.
            self._port = serial.Serial(
                s["port"], s["baud"], dsrdtr=True, timeout=s["timeout"],
            )
        except (serial.SerialException, OSError) as exc:
            self.link_changed.emit(False, str(exc))
            self.traffic.emit("ERR", f"cannot open {s['port']}: {exc}")
            return False

        self.traffic.emit("INF", f"opened {s['port']} at {s['baud']} baud (8N1, DTR/DSR)")
        self._tune_latency(s["port"])

        idn = self._exchange(b"*IDN?\n", Kind.QUERY, timeout=3.0)
        if idn is None:
            self.link_changed.emit(False, "no response to *IDN?")
            self.traffic.emit("ERR", "no response to *IDN? -- check cable, baud and DTR")
            self._close()
            return False

        # The first reply after opening the port often carries a leading
        # non-printable byte from the DTR/DSR line settling. Drop control
        # characters before displaying or matching, otherwise a stray 0x00
        # makes a valid Agilent look "unexpected" and prints a blank line.
        identity = "".join(
            ch for ch in idn.decode("ascii", "replace") if ch.isprintable()
        ).strip()
        self.traffic.emit("RX", identity)

        # Match the vendor anywhere in the string, and accept the Keysight /
        # HP names the 546xx family has shipped under.
        if not any(v in identity.upper() for v in ("AGILENT", "KEYSIGHT", "HEWLETT")):
            self.traffic.emit("ERR", f"unexpected instrument: {identity}")

        self.link_changed.emit(True, identity)
        return True

    def _tune_latency(self, device: str):
        """
        Drop the USB-serial latency timer from its 16 ms default to 1 ms.

        Every request/response pair pays this once. At small point counts it
        is a large fraction of the frame time, so it directly caps the
        achievable refresh rate. Failure is non-fatal -- the file only exists
        for FTDI adapters and often needs root.
        """
        name = device.rsplit("/", 1)[-1]
        path = f"/sys/bus/usb-serial/devices/{name}/latency_timer"
        try:
            with open(path) as fh:
                current = fh.read().strip()
            if current != "1":
                with open(path, "w") as fh:
                    fh.write("1")
                self.traffic.emit("INF", f"latency_timer {current} ms -> 1 ms")
        except OSError:
            pass

    def _close(self):
        if self._port and self._port.is_open:
            try:
                self._port.close()
            except OSError:
                pass
        self._port = None
        self.link_changed.emit(False, "disconnected")

    # -- low level exchange ------------------------------------------------

    def _read_exactly(self, n: int) -> bytes:
        """Read exactly n bytes, looping because read() may return short."""
        buf = bytearray()
        while len(buf) < n:
            chunk = self._port.read(n - len(buf))
            if not chunk:
                raise TimeoutError(f"timed out after {len(buf)} of {n} bytes")
            buf.extend(chunk)
        return bytes(buf)

    def _drain_until_quiet(self, quiet=0.3, cap=3.0):
        """
        Discard input until the line has been silent for `quiet` seconds.

        A plain reset_input_buffer() only clears bytes that have already
        arrived. After a timeout the abandoned response is often still in
        flight -- it lands a moment later and, if not consumed, gets paired
        with the next request, desynchronising every exchange from then on.
        Waiting for a sustained gap guarantees the straggler is fully read
        and thrown away. At 57600 baud a 1000-byte block takes ~0.18 s, so a
        0.3 s window comfortably clears one.
        """
        if not self._port or not self._port.is_open:
            return
        try:
            self._port.reset_input_buffer()
            start = time.monotonic()
            last_activity = start
            while time.monotonic() - last_activity < quiet:
                waiting = self._port.in_waiting
                if waiting:
                    self._port.read(waiting)
                    last_activity = time.monotonic()
                else:
                    time.sleep(0.02)
                if time.monotonic() - start > cap:
                    break
        except (serial.SerialException, OSError):
            pass

    def _resync(self):
        """Light recovery after a single failed command exchange."""
        self._drain_until_quiet(quiet=0.2)

    def _hard_resync(self):
        """
        Full recovery for the streaming path.

        Besides draining the line to silence, it forgets the active source
        and cached preambles so the next cycle re-establishes a known state
        with an explicit :WAVeform:SOURce rather than trusting the scope to
        still be where we left it.
        """
        self._active_source = None
        self._preambles.clear()
        self._drain_until_quiet(quiet=0.3)

    def _exchange(self, command: bytes, kind: int, timeout: float, tag: str = ""):
        """Write one command and read its response. Returns None on failure."""
        if not self._port or not self._port.is_open:
            return None

        previous = self._port.timeout
        self._port.timeout = timeout
        try:
            self._port.write(command)
            self._port.flush()

            if kind == Kind.WRITE:
                return b""

            if kind == Kind.QUERY:
                line = self._port.readline()
                if not line:
                    raise TimeoutError("no response")
                return line.strip()

            # Kind.BLOCK -- binary, may contain 0x0A, so never readline()
            started = time.monotonic()
            received = [0]

            def read_byte():
                return self._port.read(1)

            def read_exactly(n):
                data = self._read_exactly(n)
                received[0] += len(data)
                if received[0] > 4096:
                    self.bulk_progress.emit(received[0], time.monotonic() - started)
                return data

            return read_ieee_block(read_exactly, read_byte)

        except (TimeoutError, IOError, ValueError, serial.SerialException) as exc:
            self._resync()
            self.traffic.emit("ERR", f"{tag or command.decode('ascii', 'replace').strip()}: {exc}")
            return None
        finally:
            if self._port and self._port.is_open:
                self._port.timeout = previous

    def _service(self, request: Request):
        text = request.command.decode("ascii", "replace").strip()
        if request.echo:
            self.traffic.emit("TX", text)

        if request.solo:
            # Take exclusive ownership of the wire: pause streaming and clear
            # any waveform reply still draining from the last cycle before the
            # long transfer starts, then hand the line back clean afterwards.
            self._suspend_streaming = True
            self._hard_resync()

        try:
            result = self._exchange(request.command, request.kind,
                                    request.timeout, request.tag)
        finally:
            if request.solo:
                self._hard_resync()
                self._suspend_streaming = False

        if result is None:
            self.failed.emit(request.tag, "timed out")
            return

        if request.kind != Kind.WRITE:
            if request.echo and request.kind == Kind.QUERY:
                self.traffic.emit("RX", result.decode("ascii", "replace"))
            elif request.kind == Kind.BLOCK:
                self.traffic.emit("RX", f"<{len(result)} byte block>")
            self.replied.emit(request.tag, result)

    # -- streaming ---------------------------------------------------------

    def _stream_once(self):
        """
        Fetch one frame for every selected source.

        Steady state costs a single round trip per source: the source select
        and the preamble are both cached and only re-sent when something has
        actually changed.
        """
        if not self._sources:
            return

        # A strict request/response protocol means the input buffer should be
        # empty at the top of every cycle. Anything substantial sitting here
        # is a late reply from a transaction we already gave up on; leaving it
        # would pair it with this cycle's first query and desync every
        # exchange from now on. Discarding it is the single most important
        # guard for a slow scope, and it makes the desync self-healing rather
        # than permanent.
        #
        # A :WAVeform:DATA? block is LF-terminated, so one or two trailing
        # bytes routinely linger after the block read -- that is not a desync.
        # read_ieee_block skips leading non-'#' bytes anyway, so a lone
        # terminator is harmless. Only a larger backlog means a whole stale
        # response is queued, and only then do we force a fresh SOURce.
        try:
            waiting = self._port.in_waiting
            if waiting:
                self._port.reset_input_buffer()
                if waiting > 4:
                    self._active_source = None
        except (serial.SerialException, OSError):
            return

        started = time.monotonic()

        for source in self._sources:
            if self._stopping or not self._streaming:
                return

            if source != self._active_source:
                if self._exchange(f":WAVeform:SOURce {source}\n".encode(), Kind.WRITE,
                                  1.0, "wav:source") is None:
                    self._hard_resync()
                    return
                self._active_source = source
                self._preambles.pop(source, None)

            if self._preamble_dirty:
                if not self._apply_capture_settings():
                    self._hard_resync()
                    return

            if source not in self._preambles:
                preamble = self._read_preamble()
                if preamble is None:
                    self._hard_resync()
                    return
                self._preambles[source] = preamble

            preamble = self._preambles[source]

            # Generous timeout: 2000 WORD points is ~0.7 s of wire time alone.
            payload = self._exchange(b":WAVeform:DATA?\n", Kind.BLOCK, 6.0, "wav:data")
            if payload is None:
                self._hard_resync()
                return

            try:
                if source.startswith("POD"):
                    times, values = protocol.decode_pod(payload, preamble)
                else:
                    times, values = protocol.decode_analog(payload, preamble, self._format)
            except ValueError as exc:
                self.traffic.emit("ERR", f"decode {source}: {exc}")
                self._hard_resync()
                return

            self.frame_ready.emit(source, times, values, preamble)

        self._note_frame(started)

    _NUMBER = re.compile(rb"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

    def _query_number(self, command: bytes):
        """Query a single NR3 value, tolerating leading junk bytes."""
        raw = self._exchange(command, Kind.QUERY, 2.0,
                             command.decode("ascii", "replace").strip())
        if raw is None:
            return None
        match = self._NUMBER.search(raw)
        return float(match.group()) if match else None

    def _read_preamble(self):
        """
        Build a Preamble from the individual X/Y scaling queries.

        The 54622D does not answer the combined :WAVeform:PREamble? query --
        it returns an empty line -- but the individual increment/origin/
        reference queries work. This is the sequence the original CLI used
        against this same instrument. The result is cached per source, so
        these six round trips are paid only when the scaling changes, not on
        every frame.
        """
        values = [
            self._query_number(b":WAVeform:XINCrement?\n"),
            self._query_number(b":WAVeform:XORigin?\n"),
            self._query_number(b":WAVeform:XREFerence?\n"),
            self._query_number(b":WAVeform:YINCrement?\n"),
            self._query_number(b":WAVeform:YORigin?\n"),
            self._query_number(b":WAVeform:YREFerence?\n"),
        ]
        if any(v is None for v in values):
            self.traffic.emit("ERR", "waveform preamble query returned no value")
            return None

        return Preamble(
            format=0, type=0, points=self._points, count=1,
            x_increment=values[0], x_origin=values[1], x_reference=values[2],
            y_increment=values[3], y_origin=values[4], y_reference=values[5],
        )

    def _apply_capture_settings(self) -> bool:
        """Push format and point count, then let the preamble be re-read."""
        fmt = WaveFormat.BYTE if self._active_source and self._active_source.startswith("POD") \
            else self._format
        for cmd in (
            f":WAVeform:FORMat {fmt.scpi}\n",
            ":WAVeform:BYTeorder MSBFirst\n",
            ":WAVeform:UNSigned 1\n",
            f":WAVeform:POINts {self._points}\n",
        ):
            if self._exchange(cmd.encode(), Kind.WRITE, 1.0, "wav:setup") is None:
                return False
        self._preamble_dirty = False
        self._preambles.clear()
        return True

    def _note_frame(self, started: float):
        now = time.monotonic()
        self._frame_times.append(now)
        cutoff = now - 3.0
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.pop(0)
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            if span > 0:
                self.rate_changed.emit((len(self._frame_times) - 1) / span)
