import sys

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

from gxyfi import db
from gxyfi.main_window import MainWindow
from gxyfi.theme import STYLESHEET


def apply_palette(app):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0C1016"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#E7E1D6"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#141922"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#171C25"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#E7E1D6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1A160F"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#E7E1D6"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2A2418"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#F4E7C6"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#141922"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#F4E7C6"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#7C776C"))
    app.setPalette(palette)


def main():
    db.init_db()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("gxyFi")
    app.setApplicationDisplayName("gxyFi")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    apply_palette(app)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()
