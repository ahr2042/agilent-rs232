"""
SCPI protocol layer for Agilent 546xx / 5000-series oscilloscopes.

Everything in this module is pure: it parses and formats bytes, and knows
what the instrument supports. It never touches a serial port, so it can be
exercised without hardware.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np

# ---------------------------------------------------------------------------
# Instrument capabilities
# ---------------------------------------------------------------------------

# The 54622D is a mixed-signal scope: two analog channels plus sixteen digital
# channels grouped into two eight-bit pods. Analog bandwidth is 100 MHz and the
# ADC is 8 bits, which is why BYTE is the natural transfer format (see
# WaveFormat below).
ANALOG_CHANNELS = ("CHAN1", "CHAN2")
DIGITAL_PODS = ("POD1", "POD2")

# Vertical scales the front panel steps through, in volts/div.
VOLTS_PER_DIV = (
    0.002, 0.005, 0.010, 0.020, 0.050, 0.100, 0.200, 0.500,
    1.0, 2.0, 5.0,
)

# Timebase settings, in seconds/div.
SECONDS_PER_DIV = (
    5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1.0, 2.0, 5.0,
)

# :WAVeform:POINts only accepts this set outside of MAXimum.
POINT_COUNTS = (100, 250, 500, 1000, 2000)

# Baud rates the 546xx RS-232 port supports. 57600 is the ceiling -- there is
# no faster UART setting, which is what bounds the achievable refresh rate.
BAUD_RATES = (9600, 19200, 38400, 57600)

# The screen grid is 10 divisions wide by 8 tall.
H_DIVISIONS = 10
V_DIVISIONS = 8


class WaveFormat(Enum):
    """
    Wire format for :WAVeform:DATA?.

    The 546xx ADC is 8 bits. In NORMal acquisition mode WORD therefore carries
    eight bits of real information padded to sixteen -- twice the bytes for no
    extra resolution. BYTE is the right default; WORD only earns its bandwidth
    in AVERage mode, where the scope accumulates genuine sub-LSB precision.
    """

    BYTE = ("BYTE", 1, ">u1")
    WORD = ("WORD", 2, ">u2")

    def __init__(self, scpi: str, width: int, dtype: str):
        self.scpi = scpi
        self.width = width
        self.dtype = dtype


class AcquireType(Enum):
    NORMAL = "NORMal"
    AVERAGE = "AVERage"
    PEAK = "PEAK"


# ---------------------------------------------------------------------------
# IEEE 488.2 definite-length block
# ---------------------------------------------------------------------------


def read_ieee_block(read_exactly, read_byte):
    """
    Decode one IEEE 488.2 definite-length arbitrary block.

    Format:  # <N> <N digits of length> <payload>

    `read_byte` returns a single byte (or b'' on timeout); `read_exactly`
    returns exactly n bytes or raises. Reading is delegated so this stays
    testable and usable from the I/O thread without importing pyserial.

    A line-oriented read must never be used here: the payload is binary and
    routinely contains 0x0A, which would truncate it silently.
    """
    # Skip any leading whitespace or stale bytes until the '#' marker.
    for _ in range(64):
        c = read_byte()
        if not c:
            raise TimeoutError("timed out waiting for IEEE block marker '#'")
        if c == b"#":
            break
    else:
        raise IOError("no IEEE block marker found in the first 64 bytes")

    digits = read_exactly(1)
    if not digits.isdigit():
        raise IOError(f"malformed IEEE block length prefix: {digits!r}")

    n = int(digits)
    if n == 0:
        raise IOError("indefinite-length IEEE blocks are not supported")

    length = int(read_exactly(n))
    return read_exactly(length)


# ---------------------------------------------------------------------------
# Waveform preamble
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preamble:
    """
    Scaling parameters returned by :WAVeform:PREamble?.

    One query replaces the seven individual XINCrement?/XORigin?/... queries.
    At a typical USB-serial adapter's 16 ms turnaround that is ~175 ms saved
    per frame, which dominates the frame time at small point counts.
    """

    format: int
    type: int
    points: int
    count: int
    x_increment: float
    x_origin: float
    x_reference: float
    y_increment: float
    y_origin: float
    y_reference: float

    @classmethod
    def parse(cls, response: bytes) -> "Preamble":
        fields = response.decode("ascii", "replace").strip().split(",")
        if len(fields) < 10:
            # Keep the snippet short: a desynced read can return a whole
            # binary waveform block here, and the full repr must not reach a
            # log line or a status bar.
            raise ValueError(
                f"expected 10 preamble fields, got {len(fields)}: {response[:32]!r}...")
        return cls(
            format=int(float(fields[0])),
            type=int(float(fields[1])),
            points=int(float(fields[2])),
            count=int(float(fields[3])),
            x_increment=float(fields[4]),
            x_origin=float(fields[5]),
            x_reference=float(fields[6]),
            y_increment=float(fields[7]),
            y_origin=float(fields[8]),
            y_reference=float(fields[9]),
        )

    @property
    def acquire_type(self) -> str:
        return {0: "NORMAL", 1: "PEAK", 2: "AVERAGE", 3: "HRESOLUTION"}.get(self.type, "NORMAL")

    @property
    def sample_rate(self) -> float:
        return 1.0 / self.x_increment if self.x_increment else 0.0

    def time_axis(self, n: int) -> np.ndarray:
        """Sample index -> seconds, per the 5000-series Programmer's Guide."""
        idx = np.arange(n, dtype=np.float64)
        return (idx - self.x_reference) * self.x_increment + self.x_origin

    def to_volts(self, raw: np.ndarray) -> np.ndarray:
        """
        Raw ADC codes -> volts.

        Code 0 marks a sample the scope never filled in; those become NaN so
        gaps break the trace instead of being drawn as a spike to the bottom
        of the screen.
        """
        volts = (raw.astype(np.float64) - self.y_reference) * self.y_increment + self.y_origin
        return np.where(raw == 0, np.nan, volts)


def decode_analog(payload: bytes, preamble: Preamble, fmt: WaveFormat):
    """Decode an analog channel block into (times, volts)."""
    raw = np.frombuffer(payload, dtype=np.dtype(fmt.dtype))
    return preamble.time_axis(len(raw)), preamble.to_volts(raw)


def decode_pod(payload: bytes, preamble: Preamble):
    """
    Decode a digital pod block.

    A pod transfers one byte per sample where each bit is one digital channel,
    so this unpacks to an (8, n) array of 0/1 with row 0 = the pod's lowest
    numbered channel.
    """
    raw = np.frombuffer(payload, dtype=np.uint8)
    bits = np.unpackbits(raw[:, None], axis=1, bitorder="little").T
    return preamble.time_axis(len(raw)), bits


# ---------------------------------------------------------------------------
# Command catalogue (drives the Command Library panel)
# ---------------------------------------------------------------------------

COMMAND_LIBRARY = {
    "System Control": [
        ("*IDN?", "Identify instrument"),
        ("*RST", "Reset to defaults"),
        ("*CLS", "Clear status registers"),
        (":SYSTem:ERRor?", "Pop next error from the queue"),
        (":SYSTem:DSP \"text\"", "Write a message to the scope display"),
    ],
    "Acquisition": [
        (":RUN", "Start continuous acquisition"),
        (":STOP", "Halt acquisition"),
        (":SINGle", "Arm a single acquisition"),
        (":AUToscale", "Autoscale to the applied signal"),
        (":ACQuire:TYPE NORMal|AVERage|PEAK", "Acquisition mode"),
        (":ACQuire:COUNt <n>", "Averaging count"),
        (":ACQuire:SRATe?", "Current sample rate"),
    ],
    "Vertical": [
        (":CHANnel<n>:SCALe <v>", "Volts per division"),
        (":CHANnel<n>:OFFSet <v>", "Vertical offset"),
        (":CHANnel<n>:COUPling AC|DC", "Input coupling (1 MOhm only)"),
        (":CHANnel<n>:BWLimit ON|OFF", "20 MHz bandwidth limit"),
        (":CHANnel<n>:PROBe <x>", "Probe attenuation factor"),
        (":CHANnel<n>:INVert ON|OFF", "Invert the trace"),
    ],
    "Horizontal": [
        (":TIMebase:SCALe <s>", "Seconds per division"),
        (":TIMebase:POSition <s>", "Horizontal delay"),
        (":TIMebase:MODE MAIN|DELayed|XY|ROLL", "Timebase mode"),
    ],
    "Trigger": [
        (":TRIGger:SWEep AUTO|NORMal", "Trigger sweep mode"),
        (":TRIGger:EDGE:SOURce CHANnel<n>", "Edge trigger source"),
        (":TRIGger:EDGE:LEVel <v>", "Trigger level"),
        (":TRIGger:EDGE:SLOPe POSitive|NEGative", "Trigger slope"),
        (":TRIGger:HOLDoff <s>", "Trigger holdoff"),
        (":TRIGger:NREJect ON|OFF", "Noise reject"),
    ],
    "Digital": [
        (":POD<n>:DISPlay ON|OFF", "Show or hide a digital pod"),
        (":POD<n>:THReshold <v>", "Pod logic threshold"),
        (":DIGital<n>:DISPlay ON|OFF", "Show or hide one digital channel"),
    ],
    "Waveform": [
        (":WAVeform:SOURce CHANnel<n>|POD<n>", "Select the transfer source"),
        (":WAVeform:FORMat BYTE|WORD|ASCii", "Transfer format"),
        (":WAVeform:POINts <n>", "100 | 250 | 500 | 1000 | 2000 | MAXimum"),
        (":WAVeform:PREamble?", "All scaling parameters in one query"),
        (":WAVeform:DATA?", "Transfer the sample block"),
    ],
    "Display": [
        (":DISPlay:DATA? BMP", "Screen bitmap (slow: ~30 s at 57600 baud)"),
        (":DISPlay:CLEar", "Clear the display"),
    ],
}


def channel_label(source: str) -> str:
    """'CHAN1' -> 'CH1', 'POD1' -> 'POD1'."""
    if source.startswith("CHAN"):
        return "CH" + source[4:]
    return source


def format_si(value: float, unit: str, digits: int = 3) -> str:
    """Format a value with an SI prefix, e.g. 0.002 -> '2.00 mV'."""
    if value is None or not np.isfinite(value):
        return "--"

    # Percent and dimensionless quantities are never SI-prefixed: a standard
    # deviation of 0.0292 % must not be rendered as "29.2 m%". Percentages
    # get an extra significant figure so a duty cycle of 50.02 % survives.
    if unit in ("%", ""):
        return f"{value:.{digits + 1}g} {unit}".strip()

    if value == 0:
        return f"0.00 {unit}"

    magnitude = abs(value)
    for factor, prefix in (
        (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
        (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
    ):
        if magnitude >= factor:
            return f"{value / factor:.{digits}g} {prefix}{unit}"
    return f"{value:.{digits}g} {unit}"
