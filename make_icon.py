"""Draw the gxyFi icon and pack it as a Windows .ico."""

import struct
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QFont
from PyQt6.QtWidgets import QApplication

OUT = Path(__file__).resolve().parent / "assets"
SIZES = (16, 32, 48, 256)


def draw(size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor("#0C1016"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = max(1, size // 16)
    painter.setPen(QPen(QColor("#C6A15B"), max(1, size // 32)))
    painter.setBrush(QColor("#141922"))
    painter.drawRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, size * 0.18, size * 0.18)
    painter.setPen(QColor("#E4C98A"))
    font = QFont("Palatino Linotype")
    font.setPixelSize(int(size * 0.62))
    painter.setFont(font)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "g")
    painter.end()
    path = OUT / f"icon-{size}.png"
    image.save(str(path), "PNG")
    return path.read_bytes()


def pack_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    count = len(pngs)
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    payload = b""
    offset = 6 + 16 * count
    for size, png in pngs:
        stored = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return header + entries + payload


def main():
    app = QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    pngs = [(size, draw(size)) for size in SIZES]
    ico = OUT / "gxyfi.ico"
    ico.write_bytes(pack_ico(pngs))
    print(ico, ico.stat().st_size)


if __name__ == "__main__":
    main()
