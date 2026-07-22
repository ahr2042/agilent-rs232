"""
Design tokens and stylesheet, taken from the Stitch "Precision Engineering
Interface" design system for this project.
"""

from PyQt6.QtGui import QFont, QFontDatabase

# -- colour tokens ----------------------------------------------------------

BACKGROUND = "#09151a"
SURFACE = "#09151a"
SURFACE_LOWEST = "#041015"
SURFACE_LOW = "#111d23"
SURFACE_CONTAINER = "#152127"
SURFACE_HIGH = "#202c32"
SURFACE_HIGHEST = "#2a363d"

ON_SURFACE = "#d7e4ec"
ON_SURFACE_VARIANT = "#d3c5ad"
OUTLINE = "#9c8f79"
OUTLINE_VARIANT = "#4f4633"

PRIMARY = "#ffe2aa"
PRIMARY_CONTAINER = "#fbc02d"      # amber -- the accent colour
ON_PRIMARY_CONTAINER = "#6c5000"
ON_PRIMARY = "#402d00"

SECONDARY = "#44d8f1"              # cyan -- CH1
SECONDARY_CONTAINER = "#00bcd4"
TERTIARY = "#bbf777"               # green -- CH2
TERTIARY_CONTAINER = "#a0da5e"

ERROR = "#ffb4ab"
ERROR_CONTAINER = "#93000a"

# Trace colours, matching the design's waveform panel.
TRACE_COLOURS = {
    "CHAN1": SECONDARY,
    "CHAN2": TERTIARY,
    "POD1": PRIMARY_CONTAINER,
    "POD2": "#ff8a65",
}

GRID = "#1d3038"
GRID_AXIS = "#2c4750"

# Console log colours by direction.
LOG_COLOURS = {
    "TX": PRIMARY_CONTAINER,
    "RX": SECONDARY,
    "INF": ON_SURFACE_VARIANT,
    "ERR": ERROR,
}


def mono_font(size: int = 10, bold: bool = False) -> QFont:
    """JetBrains Mono if present, otherwise the best available monospace."""
    families = QFontDatabase.families()
    for name in ("JetBrains Mono", "JetBrainsMono Nerd Font", "Fira Code",
                 "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono"):
        if name in families:
            font = QFont(name, size)
            break
    else:
        font = QFont()
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(size)

    font.setBold(bold)
    return font


STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {ON_SURFACE};
    font-size: 11px;
}}

QMainWindow, QDialog {{ background-color: {BACKGROUND}; }}

/* -- panels ------------------------------------------------------------ */

QFrame#panel {{
    background-color: {SURFACE_CONTAINER};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 4px;
}}

QFrame#panelFlat {{
    background-color: {SURFACE_LOW};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 4px;
}}

QLabel#panelTitle {{
    color: {ON_SURFACE_VARIANT};
    font-weight: bold;
    letter-spacing: 1px;
    padding: 6px 8px;
    background-color: {SURFACE_HIGH};
    border-bottom: 1px solid {OUTLINE_VARIANT};
}}

QLabel#sectionLabel {{
    color: {OUTLINE};
    font-size: 10px;
    letter-spacing: 1px;
}}

QLabel#readout {{
    color: {PRIMARY_CONTAINER};
    font-size: 15px;
    font-weight: bold;
}}

QLabel#readoutSmall {{ color: {ON_SURFACE}; font-size: 12px; font-weight: bold; }}

/* -- navigation rail --------------------------------------------------- */

QFrame#navRail {{
    background-color: {SURFACE_LOWEST};
    border-right: 1px solid {OUTLINE_VARIANT};
}}

QPushButton#navButton {{
    background-color: transparent;
    border: none;
    border-left: 2px solid transparent;
    color: {OUTLINE};
    padding: 10px 6px;
    text-align: center;
    font-size: 9px;
    letter-spacing: 1px;
}}

QPushButton#navButton:hover {{ background-color: {SURFACE_LOW}; color: {ON_SURFACE}; }}

QPushButton#navButton:checked {{
    background-color: {SURFACE_CONTAINER};
    border-left: 2px solid {PRIMARY_CONTAINER};
    color: {PRIMARY_CONTAINER};
}}

/* -- buttons ----------------------------------------------------------- */

QPushButton {{
    background-color: {SURFACE_HIGH};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 3px;
    padding: 5px 12px;
    color: {ON_SURFACE};
}}

QPushButton:hover {{ border-color: {OUTLINE}; background-color: {SURFACE_HIGHEST}; }}
QPushButton:pressed {{ background-color: {SURFACE_LOW}; }}
QPushButton:disabled {{ color: {OUTLINE_VARIANT}; border-color: {OUTLINE_VARIANT}; }}

QPushButton#accent {{
    background-color: {PRIMARY_CONTAINER};
    color: {ON_PRIMARY};
    border: none;
    font-weight: bold;
    letter-spacing: 1px;
}}
QPushButton#accent:hover {{ background-color: {PRIMARY}; }}

QPushButton#danger {{
    background-color: {ERROR_CONTAINER};
    color: {ERROR};
    border: 1px solid {ERROR};
    font-weight: bold;
    letter-spacing: 1px;
}}
QPushButton#danger:hover {{ background-color: #b3000d; }}

QPushButton#segment {{
    background-color: {SURFACE_LOW};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 2px;
    padding: 4px 10px;
    color: {OUTLINE};
}}
QPushButton#segment:checked {{
    background-color: {PRIMARY_CONTAINER};
    color: {ON_PRIMARY};
    border-color: {PRIMARY_CONTAINER};
    font-weight: bold;
}}

/* -- inputs ------------------------------------------------------------ */

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {SURFACE_LOWEST};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 3px;
    padding: 4px 6px;
    color: {ON_SURFACE};
    selection-background-color: {PRIMARY_CONTAINER};
    selection-color: {ON_PRIMARY};
}}

QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {PRIMARY_CONTAINER};
}}

QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {OUTLINE};
    margin-right: 5px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_HIGH};
    border: 1px solid {OUTLINE};
    selection-background-color: {PRIMARY_CONTAINER};
    selection-color: {ON_PRIMARY};
    outline: none;
}}

QPlainTextEdit, QTextEdit {{
    background-color: {SURFACE_LOWEST};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 3px;
    selection-background-color: {PRIMARY_CONTAINER};
    selection-color: {ON_PRIMARY};
}}

/* -- sliders ----------------------------------------------------------- */

QSlider::groove:horizontal {{
    height: 3px; background: {SURFACE_HIGHEST}; border-radius: 1px;
}}
QSlider::handle:horizontal {{
    background: {PRIMARY_CONTAINER}; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px;
}}
QSlider::sub-page:horizontal {{ background: {PRIMARY_CONTAINER}; border-radius: 1px; }}

/* -- tables ------------------------------------------------------------ */

QTableWidget, QTreeWidget, QListWidget {{
    background-color: {SURFACE_LOWEST};
    border: 1px solid {OUTLINE_VARIANT};
    gridline-color: {OUTLINE_VARIANT};
    outline: none;
    alternate-background-color: {SURFACE_LOW};
}}

QTableWidget::item, QTreeWidget::item, QListWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: {SURFACE_HIGH}; color: {PRIMARY_CONTAINER};
}}

QHeaderView::section {{
    background-color: {SURFACE_HIGH};
    color: {ON_SURFACE_VARIANT};
    border: none;
    border-right: 1px solid {OUTLINE_VARIANT};
    border-bottom: 1px solid {OUTLINE_VARIANT};
    padding: 5px;
    font-size: 10px;
    letter-spacing: 1px;
}}

/* -- tabs -------------------------------------------------------------- */

QTabWidget::pane {{ border: 1px solid {OUTLINE_VARIANT}; background: {SURFACE_CONTAINER}; }}
QTabBar::tab {{
    background: {SURFACE_LOW};
    color: {OUTLINE};
    padding: 6px 16px;
    border: 1px solid {OUTLINE_VARIANT};
    border-bottom: none;
    letter-spacing: 1px;
    font-size: 10px;
}}
QTabBar::tab:selected {{ background: {SURFACE_CONTAINER}; color: {PRIMARY_CONTAINER}; }}

/* -- misc -------------------------------------------------------------- */

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {OUTLINE}; border-radius: 2px;
    background: {SURFACE_LOWEST};
}}
QCheckBox::indicator:checked {{
    background: {PRIMARY_CONTAINER}; border-color: {PRIMARY_CONTAINER};
}}

QGroupBox {{
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {ON_SURFACE_VARIANT};
    letter-spacing: 1px;
    font-size: 10px;
}}

QScrollBar:vertical {{ background: {SURFACE_LOWEST}; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SURFACE_HIGHEST}; border-radius: 4px; min-height: 20px; }}
QScrollBar::handle:vertical:hover {{ background: {OUTLINE}; }}
QScrollBar:horizontal {{ background: {SURFACE_LOWEST}; height: 9px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_HIGHEST}; border-radius: 4px; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{
    background: {SURFACE_LOWEST};
    border: 1px solid {OUTLINE_VARIANT};
    border-radius: 2px;
    text-align: center;
    color: {ON_SURFACE};
    height: 14px;
}}
QProgressBar::chunk {{ background: {PRIMARY_CONTAINER}; }}

QSplitter::handle {{ background: {OUTLINE_VARIANT}; }}
QToolTip {{
    background: {SURFACE_HIGH}; color: {ON_SURFACE};
    border: 1px solid {OUTLINE};
}}
QStatusBar {{ background: {SURFACE_LOWEST}; border-top: 1px solid {OUTLINE_VARIANT}; }}
QMenuBar {{ background: {SURFACE_LOWEST}; }}
QMenuBar::item:selected {{ background: {SURFACE_HIGH}; color: {PRIMARY_CONTAINER}; }}
QMenu {{ background: {SURFACE_HIGH}; border: 1px solid {OUTLINE}; }}
QMenu::item:selected {{ background: {PRIMARY_CONTAINER}; color: {ON_PRIMARY}; }}
"""
