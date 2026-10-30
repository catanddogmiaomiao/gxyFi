STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background: #0C1016;
    color: #E7E1D6;
}
QWidget {
    color: #E7E1D6;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
#Page, #Sidebar, QStatusBar {
    background: #0C1016;
}
#Sidebar {
    background: #090C11;
    border-right: 1px solid #2A2418;
}
#GoldLine {
    background: #C6A15B;
    border: none;
}
#Brand {
    color: #E4C98A;
    font-family: "Palatino Linotype", "Segoe UI";
    font-size: 26px;
    letter-spacing: 2px;
}
#BrandSub, #SideFoot, #TopMeta, #PageSub, #EmptyHint, #MetricNote {
    color: #8C877C;
    font-size: 12px;
}
#SideFoot {
    padding: 0 18px 18px 18px;
}
#PageTitle {
    color: #F4E7C6;
    font-size: 24px;
    font-family: "Palatino Linotype", "Microsoft YaHei UI";
}
#Kicker {
    color: #C6A15B;
    font-size: 12px;
    letter-spacing: 1px;
}
#MetricValue {
    color: #F4E7C6;
    font-family: "Segoe UI";
    font-size: 26px;
}
#MetricCard, #Panel {
    background: #141922;
    border: 1px solid #2C261C;
    border-top: 1px solid #C6A15B;
    border-radius: 8px;
}
#NavButton {
    text-align: left;
    padding: 12px 18px;
    border: none;
    border-left: 2px solid transparent;
    color: #A39E94;
    background: transparent;
    font-size: 14px;
}
#NavButton:hover {
    color: #F4E7C6;
    background: #12100C;
}
#NavButton:checked {
    color: #F4E7C6;
    background: #16130E;
    border-left: 2px solid #C6A15B;
}
QPushButton#PrimaryButton, QPushButton#GhostButton, QPushButton#DangerButton {
    padding: 8px 16px;
    border-radius: 4px;
}
QPushButton#PrimaryButton {
    background: #C6A15B;
    color: #1A140C;
    border: none;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background: #D4B56E;
}
QPushButton#PrimaryButton:disabled {
    background: #5C5344;
    color: #1A140C;
}
QPushButton#GhostButton {
    background: transparent;
    color: #E7E1D6;
    border: 1px solid #3A3428;
}
QPushButton#GhostButton:hover {
    border-color: #C6A15B;
    color: #F4E7C6;
}
QPushButton#DangerButton {
    background: transparent;
    color: #C4786A;
    border: 1px solid #6A403A;
}
QLineEdit, QComboBox, QDateEdit, QSpinBox {
    background: #10151C;
    color: #F4E7C6;
    border: 1px solid #3A3428;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #2A2418;
    selection-color: #F4E7C6;
    min-height: 18px;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {
    border-color: #C6A15B;
}
QComboBox::drop-down, QDateEdit::drop-down, QSpinBox::up-button, QSpinBox::down-button {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background: #141922;
    color: #E7E1D6;
    selection-background-color: #2A2418;
    selection-color: #F4E7C6;
    border: 1px solid #3A3428;
    outline: none;
}
QTableWidget {
    background: #12171F;
    alternate-background-color: #171C25;
    color: #E7E1D6;
    gridline-color: transparent;
    border: 1px solid #2C261C;
    border-radius: 8px;
    selection-background-color: #2A2418;
    selection-color: #F4E7C6;
}
QHeaderView::section {
    background: #1A160F;
    color: #C6A15B;
    border: none;
    border-bottom: 1px solid #2C261C;
    padding: 8px;
    font-size: 12px;
}
QTableWidget::item {
    padding: 4px 8px;
}
QStatusBar {
    color: #8C877C;
    border-top: 1px solid #2A2418;
}
QScrollBar:vertical {
    background: #0C1016;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3A3428;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
    height: 0;
}
QScrollBar:horizontal {
    background: #0C1016;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: #3A3428;
    min-width: 24px;
    border-radius: 4px;
}
QToolTip {
    background: #141922;
    color: #F4E7C6;
    border: 1px solid #C6A15B;
}
QSplitter::handle {
    background: #2A2418;
}
"""
