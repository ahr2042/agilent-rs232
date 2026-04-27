#!/usr/bin/python3

import serial
import matplotlib.pyplot as plt
import argparse

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
parser.add_argument("--output",  "-o", help="save the plot to this file (e.g. capture.png, capture.pdf)."
                                            " Format is inferred from the file extension.")

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

# ---------------------------------------------------------------------------
# Serial communication — connect and identify scope
# ---------------------------------------------------------------------------

# DTR hardware handshaking is required by the Agilent 5000 series.
# A 1-second timeout prevents readline() from blocking indefinitely.
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

ser.write(b':WAVeform:XORigin?\n');   ser.flush()
scope_x_origin = float(ser.readline())

ser.write(b':WAVeform:XREFerence?\n'); ser.flush()
scope_x_reference = float(ser.readline())

# Y-axis (voltage) scale parameters
ser.write(b':WAVeform:YINCrement?\n'); ser.flush()
scope_y_increment = float(ser.readline())

ser.write(b':WAVeform:YORigin?\n');   ser.flush()
scope_y_origin = float(ser.readline())

ser.write(b':WAVeform:YREFerence?\n'); ser.flush()
scope_y_reference = float(ser.readline())

# ---------------------------------------------------------------------------
# Retrieve raw waveform data
# ---------------------------------------------------------------------------

ser.write(b':WAVeform:DATA?\n')
ser.flush()
# Response format: #<N><L…><data bytes>
# where N is the number of digits in L, and L is the byte-count of the data block
scope_data_bytes = ser.readline()

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
# Plot
# ---------------------------------------------------------------------------

fig, ax = plt.subplots()
ax.plot(data_times, data_voltages)
ax.set_title("Oscilloscope capture (mode: " + scope_read_type.decode() + ")")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Voltage (V)")
plt.xticks(rotation=45)
plt.tight_layout()

# Save the figure to disk when --output is supplied.
# matplotlib infers the file format from the extension (.png, .pdf, .svg, …).
if args.output:
    fig.savefig(args.output, dpi=150, bbox_inches='tight')
    print("Plot saved to:", args.output)

plt.show()
