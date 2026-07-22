"""In-memory repository of screen captures and exported files."""

import io
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PIL import Image


@dataclass
class Capture:
    name: str
    stamp: str
    size: int
    image: QImage
    raw: bytes = field(repr=False, default=b"")
    thumbnail: QIcon = None


@dataclass
class Export:
    name: str
    detail: str
    stamp: str


class CaptureStore(QObject):
    """Holds screen bitmaps and a log of exports for the Data Hub views."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.captures = []
        self.exports = []

    def add_capture(self, payload: bytes) -> Capture:
        """
        Decode a scope screen bitmap.

        Pillow handles the BMP so the image can be re-saved in any format the
        user picks. A malformed payload raises, which the caller reports.
        """
        image = Image.open(io.BytesIO(payload))
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        data = image.tobytes("raw", "RGB")
        qimage = QImage(data, image.width, image.height,
                        image.width * 3, QImage.Format.Format_RGB888).copy()

        thumb = QPixmap.fromImage(qimage).scaled(
            96, 72, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

        capture = Capture(
            name=f"screen_{time.strftime('%H%M%S')}.png",
            stamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            size=len(payload),
            image=qimage,
            raw=payload,
            thumbnail=QIcon(thumb),
        )
        self.captures.insert(0, capture)
        self.changed.emit()
        return capture

    def add_export(self, name, detail):
        self.exports.insert(0, Export(name, detail, time.strftime("%H:%M:%S")))
        self.changed.emit()

    def export_all(self, directory: Path) -> int:
        directory.mkdir(parents=True, exist_ok=True)
        written = 0
        for capture in self.captures:
            if capture.image.save(str(directory / capture.name)):
                written += 1
        return written

    def clear(self):
        self.captures.clear()
        self.exports.clear()
        self.changed.emit()
