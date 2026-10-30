from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

UP = QColor("#C4786A")
DOWN = QColor("#7F9E93")
FLAT = QColor("#B7B1A6")
GOLD = QColor("#C6A15B")
INK = QColor("#8C9BAB")
BRONZE = QColor("#8A7044")
MUTED = QColor("#8C877C")


def pnl_color(kind):
    if kind == "up":
        return UP
    if kind == "down":
        return DOWN
    return FLAT


class Page(QWidget):
    def __init__(self, title, subtitle):
        super().__init__()
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 22, 28, 22)
        self.root.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("PageSub")
        sub.setWordWrap(True)
        self.root.addWidget(heading)
        self.root.addWidget(sub)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(8)
        self.root.addLayout(self.toolbar)

    def add_action(self, text, slot, kind="ghost"):
        button = QPushButton(text)
        button.setObjectName({"primary": "PrimaryButton", "danger": "DangerButton"}.get(kind, "GhostButton"))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        self.toolbar.addWidget(button)
        return button


class MetricCard(QFrame):
    def __init__(self, kicker):
        super().__init__()
        self.setObjectName("MetricCard")
        self.setMinimumHeight(118)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        label = QLabel(kicker)
        label.setObjectName("Kicker")
        self.value = QLabel("—")
        self.value.setObjectName("MetricValue")
        self.note = QLabel("")
        self.note.setObjectName("MetricNote")
        self.note.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def set_value(self, text, note=""):
        self.value.setText(text)
        self.note.setText(note)


class Panel(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("Kicker")
        layout.addWidget(label)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.body, 1)


def make_table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setStretchLastSection(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setMinimumSectionSize(72)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setWordWrap(False)
    table.verticalHeader().setDefaultSectionSize(38)
    table.setFrameShape(QFrame.Shape.NoFrame)
    table.setMinimumHeight(240)
    return table


def set_cell(table, row, col, text, row_id=None, align="left", color=None):
    item = QTableWidgetItem("" if text is None else str(text))
    flag = Qt.AlignmentFlag.AlignVCenter
    flag |= Qt.AlignmentFlag.AlignRight if align == "right" else Qt.AlignmentFlag.AlignLeft
    item.setTextAlignment(flag)
    if row_id is not None:
        item.setData(Qt.ItemDataRole.UserRole, row_id)
    if color is not None:
        item.setForeground(color)
    table.setItem(row, col, item)
    return item


def selected_id(table):
    indexes = table.selectionModel().selectedRows()
    if not indexes:
        return None
    item = table.item(indexes[0].row(), 0)
    if item is None:
        return None
    return item.data(Qt.ItemDataRole.UserRole)


def fill_rows(table, rows):
    table.setRowCount(len(rows))
    for row_index, cells in enumerate(rows):
        for col_index, cell in enumerate(cells):
            if isinstance(cell, tuple):
                set_cell(table, row_index, col_index, *cell)
            else:
                set_cell(table, row_index, col_index, cell)


class MixBar(QWidget):
    def __init__(self):
        super().__init__()
        self._parts = []
        self.setMinimumHeight(92)

    def set_parts(self, parts):
        self._parts = parts
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(max(part[1], 0) for part in self._parts)
        bounds = self.rect().adjusted(0, 8, 0, -28)
        if total <= 0:
            painter.setPen(MUTED)
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "录入资产后，这里显示储蓄、投资和不动产的结构")
            return
        left = bounds.left()
        colors = [GOLD, INK, BRONZE]
        for index, (label, value) in enumerate(self._parts):
            width = bounds.width() * max(value, 0) / total
            color = colors[index % len(colors)]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(int(left), bounds.top(), max(int(width) - 4, 0), 18, 4, 4)
            left += width
        painter.setPen(MUTED)
        font = QFont("Microsoft YaHei UI", 9)
        painter.setFont(font)
        legend = "    ".join(f"{label}  {value / total * 100:.0f}%" for label, value in self._parts if value > 0)
        painter.drawText(self.rect().adjusted(0, 36, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, legend)


class MonthBars(QWidget):
    def __init__(self):
        super().__init__()
        self._rows = []
        self.setMinimumHeight(180)

    def set_rows(self, rows):
        self._rows = rows
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows or all(value <= 0 for _label, value in self._rows):
            painter.setPen(MUTED)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "近几个月还没有支出")
            return
        peak = max(value for _label, value in self._rows) or 1
        count = len(self._rows)
        gap = 16
        width = max((self.width() - gap * (count + 1)) / count, 8)
        base = self.height() - 28
        top = 12
        for index, (label, value) in enumerate(self._rows):
            height = 0 if peak == 0 else (base - top) * value / peak
            x = gap + index * (width + gap)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(GOLD)
            painter.drawRoundedRect(int(x), int(base - height), int(width), max(int(height), 2), 3, 3)
            painter.setPen(MUTED)
            painter.drawText(int(x - 8), base + 4, int(width + 16), 20, Qt.AlignmentFlag.AlignCenter, label[2:])
            painter.setPen(QPen(QColor("#F4E7C6")))
            if value > 0:
                painter.drawText(int(x - 12), int(base - height) - 18, int(width + 24), 16, Qt.AlignmentFlag.AlignCenter, f"{value:,.0f}")
