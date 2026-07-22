# Agilent RS-232 Oscilloscope Capture

![Python 3](https://img.shields.io/badge/python-3-blue.svg)
![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)

Tools for driving an **Agilent 54621A / 54622D** oscilloscope over an RS-232
serial link:

- **`agilent-rs232.py`** — a command-line tool that captures a waveform, decodes
  it into calibrated time/voltage samples, and plots it with matplotlib. It can
  also pull the scope's on-screen display as a bitmap, with optional upscaling.
- **`agilent-gui.py`** — a Qt desktop application with live streaming, channel
  and trigger control, cursors, running measurement statistics, FFT, a SCPI
  console and a script runner.

## Why the trace is rebuilt locally

At 57600 baud — the highest rate the 546xx supports — the link carries
**5760 bytes/s**. That single number decides the whole design:

| Transfer | Payload | Time | Effective rate |
|---|---|---|---|
| Screen bitmap (`:DISPlay:DATA? BMP`) | ~170 kB | ~30 s | 0.03 frames/s |
| Waveform, 2000 pts, WORD | 4 kB | 0.69 s | 1.4 frames/s |
| Waveform, 1000 pts, WORD | 2 kB | 0.35 s | 2.9 frames/s |
| Waveform, 1000 pts, BYTE | 1 kB | 0.17 s | 5.7 frames/s |
| Waveform, 500 pts, BYTE | 0.5 kB | 0.09 s | ~10 frames/s |

Scraping the scope's screen is roughly **170× slower** than transferring the
samples and redrawing the trace on the host, so a live video feed of the display
is not achievable at any supported baud rate — it would be one frame every half
minute. The GUI therefore streams raw samples and reconstructs the waveform
locally, which is not only far faster but yields real voltages that cursors,
measurements and FFT can operate on. The screen bitmap remains available as a
deliberate one-shot capture.

Two consequences worth knowing:

- The 546xx ADC is **8 bits**, so `WORD` format spends twice the bandwidth to
  carry the same information as `BYTE` in normal acquisition. The GUI uses
  `BYTE` by default and switches to `WORD` only for averaging, where the scope
  genuinely accumulates sub-LSB precision.
- The waveform scaling (X/Y increment, origin and reference) is read once and
  cached, then re-read only when a setting actually changes, so steady-state
  streaming is a single `:WAVeform:DATA?` per frame. The 54622D does not answer
  the combined `:WAVeform:PREamble?` query, so those values are fetched with the
  six individual scaling queries the instrument does support.

> **Fork notice** — This is a fork of the original
> [`agilent-rs232`](https://01001000.xyz/2020-05-07-Walkthrough-Agilent-Oscilloscope-RS232/)
> project by kiwih, whose walkthrough blog post is an excellent companion to the
> base script. This fork adds direct screen-bitmap capture (`--output`), Lanczos
> upscaling of saved screenshots (`--scale`), and a refactor with clearer
> diagnostics and more robust binary-block parsing.

## The GUI

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python agilent-gui.py --port /dev/ttyUSB0 --connect
```

Five views, reachable from the navigation rail:

| View | Contents |
|---|---|
| **Channels** | Live trace, per-channel scale/offset/coupling/bandwidth/probe, timebase, trigger, and a SCPI console |
| **Measure** | Gated measurements with running mean, standard deviation, min and max across acquisitions |
| **Scripts** | Script editor with syntax highlighting, a run/halt interpreter, debug console and searchable command library |
| **Data** | Cursors with Δt / 1÷Δt / ΔV readouts, local FFT, screen captures and CSV export |
| **Config** | Serial port and baud, display preferences, error queue and `*RST` |

**Mixed-signal support** — the 54622D's two analog channels appear as CH1/CH2
and its sixteen digital channels as POD1 (D0–D7) and POD2 (D8–D15). Enabling a
pod adds a stacked logic lane beneath the analog trace, sharing its time axis.

**Script language** — bare SCPI lines plus `FOR i = 1 TO n` / `NEXT`, `WAIT
<ms>`, `PRINT` and `#` comments. Scripts are interpreted one statement per timer
tick, so a running script never blocks the interface and `HALT` takes effect
immediately.

Measurements available: Vpp, V-amplitude, Vmax, Vmin, V-average, V-RMS,
frequency, period, rise time, fall time and duty cycle. Amplitude, timing and
duty-cycle figures use the histogram top/base method rather than raw max/min, so
overshoot and ringing do not distort them.

### Architecture

The serial port is a single shared blocking resource, so all I/O runs on a
dedicated thread behind a priority queue and communicates with the interface
purely through Qt signals. User actions preempt the streaming poll. Because the
scope can go briefly busy after a setting change and answer a query late, every
streaming cycle starts from an empty input buffer and any failure drains the
line to silence before retrying — so a late reply can never desynchronise the
stream, and the screen-bitmap capture runs as a solo transaction that never
collides with a streamed waveform.

```
agiloscope/
    protocol.py     IEEE 488.2 blocks, preamble, decoding, command catalogue
    transport.py    threaded serial link, priority queue, streaming loop
    instrument.py   scope state and SCPI generation
    measure.py      measurements, Welford statistics, FFT
    plot.py         waveform rendering, cursors, digital lane
    store.py        screen captures and exports
    theme.py        design tokens and stylesheet
    widgets.py      panels, dials, segmented controls, badges
    views/          the five screens
```

## CLI features

- **Waveform capture** — reads up to 2000 points from channel 1 or 2 as signed
  16-bit words and converts them to calibrated volts and seconds using the
  scope's own scale/offset preamble.
- **Instant plotting** — renders the captured trace with matplotlib.
- **Screen capture** — downloads the scope's live display as a BMP and saves it
  as PNG, BMP, JPEG, or any other Pillow-supported format (`--output`).
- **Upscaling** — optionally enlarges the saved screenshot with Lanczos
  resampling (`--scale`).
- **Robust binary transfer** — parses IEEE 488.2 definite-length blocks so binary
  payloads that contain newline (`0x0A`) bytes are read correctly.

## Example

The tool reads the trace shown on the scope…

![Photo of the oscilloscope displaying a square wave](scope.jpg?raw=true "Oscilloscope display")

…and renders it locally with matplotlib:

![Terminal output and matplotlib plot of the captured waveform](reading.png?raw=true "Matplotlib rendering")

## Requirements

### Hardware

- An Agilent 54621A or 54622D oscilloscope. Other models that share the same
  RS-232 SCPI command set may also work, but are untested.
- An RS-232 connection between the scope and the host computer — typically a
  USB-to-RS-232 adapter, which appears as `/dev/ttyUSB0` on Linux by default.
- The scope's I/O configured for RS-232 with DTR/DSR hardware handshaking and a
  baud rate matching the `--baud` setting (57600 by default).

### Software

For the command-line tool:

- Python 3
- [`pyserial`](https://pypi.org/project/pyserial/) — serial communication
- [`matplotlib`](https://pypi.org/project/matplotlib/) — plotting
- [`Pillow`](https://pypi.org/project/Pillow/) — image handling (imported at
  startup, so it is required even when you are not saving a screenshot)

The GUI additionally needs [`PyQt6`](https://pypi.org/project/PyQt6/),
[`pyqtgraph`](https://pypi.org/project/pyqtgraph/) and
[`numpy`](https://pypi.org/project/numpy/); see `requirements.txt`.

## Installation

```bash
git clone https://github.com/ahr2042/agilent-rs232.git
cd agilent-rs232
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On distributions where the system Python is externally managed (Debian, Ubuntu
24.04 and later), the virtual environment above avoids `pip` refusing to
install. For the command-line tool alone, `pip install pyserial matplotlib
Pillow` is sufficient.

## Usage

```
usage: agilent-rs232.py [-h] [--port PORT] [--baud BAUD] [--channel CHANNEL]
                        [--length LENGTH] [--output OUTPUT] [--scale SCALE]

options:
  -h, --help            show this help message and exit
  --port PORT, -p PORT  serial port (default: /dev/ttyUSB0)
  --baud BAUD, -b BAUD  baud rate (default: 57600)
  --channel CHANNEL, -c CHANNEL
                        probe channel 1 or 2 (default: 1)
  --length LENGTH, -l LENGTH
                        sample count: 100, 250, 500, 1000, 2000, MAXimum
                        (default: 1000)
  --output OUTPUT, -o OUTPUT
                        save the scope's screen bitmap to this file (e.g.
                        capture.png, capture.bmp). Format is inferred from the
                        file extension.
  --scale SCALE, -s SCALE
                        upscale the saved screenshot by this factor (e.g. 2.0
                        doubles each dimension). Uses Lanczos resampling. Only
                        applies with --output.
```

### Plot a waveform

Capture 1000 points from channel 1 on the default port and plot them:

```bash
python3 agilent-rs232.py
```

Capture the maximum number of points from channel 2 on a specific port:

```bash
python3 agilent-rs232.py -p /dev/ttyUSB1 -c 2 -l MAXimum
```

The script prints the scope's acquisition mode, the X/Y scaling parameters, and
the measured voltage range, then opens a matplotlib window with the trace.

### Save the scope's screenshot

Save the scope's screen at native resolution:

```bash
python3 agilent-rs232.py -o capture.png
```

Save with 2× upscaling (512×349 → 1024×698):

```bash
python3 agilent-rs232.py -o capture.png -s 2
```

The screenshot is retrieved directly from the scope as a BMP over RS-232 and
converted to the requested format by Pillow; the output format is inferred from
the file extension. Transfer takes roughly 30 seconds at 57600 baud.

![Terminal command and the resulting 2× upscaled scope screenshot](Screenshot.png?raw=true "Screenshot capture with upscaling")

## How it works

1. **Connect & identify** — opens the serial port with DTR/DSR hardware
   handshaking and sends `*IDN?`, verifying the reply begins with `AGILENT`.
2. **Configure the transfer** — requests signed 16-bit words, MSB-first, sets the
   point count, and selects the source channel via `:WAVeform` SCPI commands.
3. **Read the preamble** — queries the X and Y increment, origin, and reference
   values that describe how to convert raw samples into real units.
4. **Fetch the data** — issues `:WAVeform:DATA?` and reads the returned
   IEEE 488.2 definite-length block.
5. **Decode & plot** — applies the scope's calibration formulas
   (`voltage = (raw − y_reference) × y_increment + y_origin`, and the analogous
   formula for time) and renders the result with matplotlib.
6. **Screenshot (optional)** — with `--output`, requests `:DISPlay:DATA? BMP`,
   reads the bitmap block, optionally upscales it, and saves it via Pillow.

## Related projects

For a more full-featured, cross-platform way to talk to oscilloscopes and other
lab instruments from Python, see the
[PyVISA](https://pyvisa.readthedocs.io/en/latest/) library.

## License

This project is licensed under the **GNU General Public License v3.0**. It is a
fork of `agilent-rs232` by kiwih, which was released under the MIT License; both
license texts are preserved in the [`LICENSE`](LICENSE) file.
