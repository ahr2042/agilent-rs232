#!/usr/bin/python3

import io
import serial
import matplotlib.pyplot as plt
import argparse
from PIL import Image

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

# Defaults
port    = "/dev/ttyUSB0"
baud    = 57600
channel = 1
length  = 1000

parser = argparse.ArgumentParser(
    description="Capture a waveform from an Agilent 5000-series oscilloscope over RS-232."
)
parser.add_argument("--port",    "-p", help="serial port (default: %s)"   % port)
parser.add_argument("--baud",    "-b", help="baud rate (default: %d)"     % baud)
parser.add_argument("--channel", "-c", help="probe channel 1 or 2 (default: %d)" % channel)
parser.add_argument("--length",  "-l", help="sample count: 100, 250, 500, 1000, 2000, MAXimum (default: %d)" % length)
parser.add_argument("--output",  "-o", help="save the scope's screen bitmap to this file "
                                            "(e.g. capture.png, capture.bmp). "
                                            "Format is inferred from the file extension.")
parser.add_argument("--scale",   "-s", type=float,
                                       help="upscale the saved screenshot by this factor "
                                            "(e.g. 2.0 doubles each dimension). "
                                            "Uses Lanczos resampling. Only applies with --output.")

args = parser.parse_args()

if args.port:
    port = args.port
if args.baud:
    baud = int(args.baud)
if args.channel:
    channel = int(args.channel)
if args.length:
    if args.length in ("100", "250", "500", "1000", "2000", "MAXimum"):
        length = args.length
    else:
        print("Invalid length (must be one of: 100, 250, 500, 1000, 2000, MAXimum)")
        exit(1)
if args.scale is not None and args.scale <= 0:
    print("Invalid scale factor (must be a positive number, e.g. 2.0)")
    exit(1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_ieee_block(ser):
    """
    Read one IEEE 488.2 definite-length arbitrary block from the serial port.

    Block format:  # <N> <L×N digits> <payload bytes>
      N        — single ASCII digit: number of digits that encode the payload length
      L        — payload length in bytes (N ASCII digits)
      payload  — L raw bytes

    readline() cannot be used here because the payload is binary and may
    contain 0x0A (\n) bytes that would terminate the read prematurely.
    """
    # Consume bytes until the mandatory '#' start marker
    while True:
        c = ser.read(1)
        if not c:
            raise IOError("Timeout waiting for IEEE block start marker '#'")
        if c == b'#':
            break

    n      = int(ser.read(1))           # number of length digits
    length = int(ser.read(n))           # payload size in bytes

    # Read exactly 'length' bytes, looping because a single read() call may
    # return fewer bytes than requested when the OS buffer isn't full yet
    data = bytearray()
    while len(data) < length:
        chunk = ser.read(length - len(data))
        if not chunk:
            raise IOError("Timeout while reading IEEE block payload")
        data.extend(chunk)

    return bytes(data)


# ---------------------------------------------------------------------------
# Serial communication — connect and identify scope
# ---------------------------------------------------------------------------

# DTR hardware handshaking is required by the Agilent 5000 series.
# A 1-second timeout prevents read() from blocking indefinitely.
ser = serial.Serial(port, baud, dsrdtr=True, timeout=1)

ser.write(b'*IDN?\n')
ser.flush()
scope_idn = ser.readline()

if scope_idn[0:7] != b'AGILENT':
    print("Unexpected response from scope — check your connection and try again.")
    ser.close()
    exit(1)

# ---------------------------------------------------------------------------
# Waveform setup
# ---------------------------------------------------------------------------

# Request signed 16-bit words (range -32768 … 32767), MSB first
ser.write(b':WAVEform:FORMat WORD\n')
ser.write(b':WAVeform:BYTeorder MSBFirst\n')
ser.write(b':WAVeform:UNSigned 0\n')

# Number of sample points to retrieve
ser.write((":WAVeform:POINts %s\n" % length).encode())

# Select the requested channel
if channel == 1:
    ser.write(b':WAVeform:SOURce CHANnel1\n')
else:
    ser.write(b':WAVeform:SOURce CHANnel2\n')

# ---------------------------------------------------------------------------
# Read waveform preamble (scale / offset parameters)
# ---------------------------------------------------------------------------

# Acquisition mode: NORM, PEAK, or AVER
ser.write(b':WAVeform:TYPE?\n')
ser.flush()
scope_read_type = ser.readline()[:-1]

# X-axis (time) scale parameters — all returned in NR3 (float) format
ser.write(b':WAVeform:XINCrement?\n'); ser.flush()
scope_x_increment = float(ser.readline())

ser.write(b':WAVeform:XORigin?\n');    ser.flush()
scope_x_origin = float(ser.readline())

ser.write(b':WAVeform:XREFerence?\n'); ser.flush()
scope_x_reference = float(ser.readline())

# Y-axis (voltage) scale parameters
ser.write(b':WAVeform:YINCrement?\n'); ser.flush()
scope_y_increment = float(ser.readline())

ser.write(b':WAVeform:YORigin?\n');    ser.flush()
scope_y_origin = float(ser.readline())

ser.write(b':WAVeform:YREFerence?\n'); ser.flush()
scope_y_reference = float(ser.readline())

# ---------------------------------------------------------------------------
# Retrieve raw waveform data
# ---------------------------------------------------------------------------

ser.write(b':WAVeform:DATA?\n')
ser.flush()
# Response format: #<N><L…><data bytes>
scope_data_bytes = ser.readline()

# ---------------------------------------------------------------------------
# Capture scope screen bitmap (only when --output is requested)
# ---------------------------------------------------------------------------

scope_bitmap = None
if args.output:
    # A full BMP over RS-232 at 57600 baud can take over two minutes;
    # raise the per-read timeout accordingly before issuing the command
    ser.timeout = 180
    # The 54622D requires an explicit format argument; BMP is the supported format
    print("Requesting screen bitmap from scope (~30 s over RS-232 at 57600 baud)…")
    ser.write(b':DISPlay:DATA? BMP\n')
    ser.flush()
    scope_bitmap = read_ieee_block(ser)
    print("Bitmap received (%d bytes)" % len(scope_bitmap))

ser.close()

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

print("Oscilloscope mode:",  scope_read_type.decode())
print("X increment (s):",    scope_x_increment)
print("X reference:",        scope_x_reference)
print("X origin (s):",       scope_x_origin)
print("Y increment (V):",    scope_y_increment)
print("Y reference:",        scope_y_reference)
print("Y origin (V):",       scope_y_origin)

# ---------------------------------------------------------------------------
# Decode raw bytes → voltage and time arrays
# ---------------------------------------------------------------------------

scope_data_preamble_len = scope_data_bytes[1] - 48           # ASCII digit → int
scope_data_len          = int(scope_data_bytes[2:2+scope_data_preamble_len])
print("Data length (bytes):", scope_data_len)

# Convert each 2-byte signed integer to a calibrated voltage.
# Formula from Agilent 5000 Series Programmer's Guide, p. 595:
#   voltage = (raw_value - y_reference) * y_increment + y_origin
data_voltages = []
for i in range(0, scope_data_len, 2):
    offset    = i + scope_data_preamble_len + 2
    raw_value = int.from_bytes(scope_data_bytes[offset:offset+2], byteorder='big', signed=True)
    voltage   = (raw_value - scope_y_reference) * scope_y_increment + scope_y_origin
    data_voltages.append(voltage)

print("Min (V):", min(data_voltages))
print("Max (V):", max(data_voltages))

# Build the corresponding time axis.
# Formula from the same reference manual (p. 595):
#   time = (sample_index - x_reference) * x_increment + x_origin
data_times = [
    (i - scope_x_reference) * scope_x_increment + scope_x_origin
    for i in range(len(data_voltages))
]

# ---------------------------------------------------------------------------
# Save scope bitmap (when --output was supplied)
# ---------------------------------------------------------------------------

if scope_bitmap is not None:
    # The scope returns a raw BMP. Pillow handles the conversion so the
    # user can request any supported format (.png, .bmp, .jpg, …) via
    # the file extension of --output.
    img = Image.open(io.BytesIO(scope_bitmap))

    if args.scale is not None:
        # Lanczos gives the sharpest result for upscaling low-resolution bitmaps
        new_w = round(img.width  * args.scale)
        new_h = round(img.height * args.scale)
        img   = img.resize((new_w, new_h), Image.LANCZOS)
        print("Upscaled to %dx%d (factor %.2f)" % (new_w, new_h, args.scale))

    img.save(args.output)
    print("Screenshot saved to:", args.output)

# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

fig, ax = plt.subplots()
ax.plot(data_times, data_voltages)
ax.set_title("Oscilloscope capture (mode: " + scope_read_type.decode() + ")")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Voltage (V)")
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()
