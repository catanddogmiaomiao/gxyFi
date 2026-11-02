import sys
import traceback
from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QMessageBox

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


def icon_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "gxyfi.ico"


def install_error_box():
    def show(kind, value, tb):
        if not isinstance(value, Exception):
            sys.__excepthook__(kind, value, tb)
            return
        parent = QApplication.activeWindow()
        if isinstance(value, ValueError):
            QMessageBox.warning(parent, "没有保存", str(value))
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, MainWindow):
                    widget._refresh_cloud_label()
            return
        detail = "".join(traceback.format_exception(kind, value, tb))
        box = QMessageBox(QMessageBox.Icon.Critical, "出错了", str(value) or kind.__name__, parent=parent)
        box.setDetailedText(detail)
        box.exec()

    sys.excepthook = show


def main():
    db.init_db()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("gxyFi")
    app.setApplicationDisplayName("gxyFi")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    icon = icon_path()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    apply_palette(app)
    app.setStyleSheet(STYLESHEET)
    install_error_box()
    window = MainWindow()
    window.show()
    return app.exec()
