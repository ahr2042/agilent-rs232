# agilent-rs232

> **Fork notice:** This is a fork of the original project. The original author wrote a blog post to go along with the base script — you can find it [here](https://01001000.xyz/2020-05-07-Walkthrough-Agilent-Oscilloscope-RS232/). This fork extends the script with screenshot capture and upscaling support.

A script to read the waveform from an Agilent 54621A/54622D oscilloscope via RS-232, display it using matplotlib, and optionally save the scope's screen as an image file.

It can convert this:

![scope](scope.jpg?raw=true "Oscilloscope reading")

To this:

![matplotlib](reading.png?raw=true "Matplotlib rendering")

# Dependencies

- `pyserial`
- `matplotlib`
- `Pillow` (required for `--output` / `--scale`)

# Usage

The program is written in Python 3.

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

## Screenshot examples

Save the scope's screen at native resolution:
```
python3 agilent-rs232.py -o capture.png
```

Save with 2× upscaling (512×349 → 1024×698):
```
python3 agilent-rs232.py -o capture.png -s 2
```

The screenshot is retrieved directly from the scope as a BMP over RS-232 and converted to the requested format by Pillow. Transfer takes approximately 30 seconds at 57600 baud.

# Other implementations

A more full-featured approach to reading scopes and other instruments from Python can be found under the [PyVisa](https://pyvisa.readthedocs.io/en/latest/) library.
