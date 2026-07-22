#!/usr/bin/env python3
"""
Qt front end for Agilent 546xx / 5000-series oscilloscopes over RS-232.

Traces are rebuilt locally from the raw sample block rather than scraped from
the instrument's screen: at 57600 baud that is roughly 170 times faster, and
it yields real voltages that cursors, measurements and FFT can work on.
"""

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from agiloscope import theme
from agiloscope.mainwindow import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", "-p", default="/dev/ttyUSB0",
                        help="serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", "-b", type=int, default=57600,
                        help="baud rate (default: 57600, the 546xx maximum)")
    parser.add_argument("--connect", "-c", action="store_true",
                        help="open the port immediately at startup")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("Agilent Signal Processor Terminal")
    app.setStyleSheet(theme.STYLESHEET)
    app.setFont(theme.mono_font(10))

    window = MainWindow(port=args.port, baud=args.baud, autoconnect=args.connect)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
