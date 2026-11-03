from PyQt6.QtCore import QRectF, Qt, pyqtSignal
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
SELECTED = QColor("#2A2418")
TRACK = QColor("#1C222C")
BRIGHT = QColor("#F4E7C6")

CATEGORY_COLORS = [
    QColor("#C6A15B"), QColor("#8C9BAB"), QColor("#7F9E93"), QColor("#C4786A"), QColor("#9A86A6"),
    QColor("#D8C79E"), QColor("#5F8A8B"), QColor("#A0695A"), QColor("#8A7044"), QColor("#6F7F95"),
]


def category_colors(names):
    """按类别的固定顺序分配颜色，同一类别在各个图里颜色一致。"""
    colors = {name: CATEGORY_COLORS[index % len(CATEGORY_COLORS)] for index, name in enumerate(names)}
    colors.setdefault("其他类别", MUTED)
    return colors


def _font(size=9, bold=False):
    font = QFont("Microsoft YaHei UI", size)
    font.setBold(bold)
    return font


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


class CategoryBars(QWidget):
    """每个类别一行：名称、占比条、金额与占比。有预算时在条上画出预算线。点一行选中，再点取消。"""

    picked = pyqtSignal(object)
    ROW = 34

    def __init__(self):
        super().__init__()
        self._rows = []
        self._colors = {}
        self._budgets = {}
        self._selected = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_rows(self, rows, colors, budgets=None, selected=None):
        self._rows = rows
        self._colors = colors
        self._budgets = budgets or {}
        self._selected = selected
        self.setMinimumHeight(max(len(rows) * self.ROW + 8, 120))
        self.update()

    def mousePressEvent(self, event):
        index = int(event.position().y() // self.ROW)
        if 0 <= index < len(self._rows):
            category_id = self._rows[index]["category_id"]
            self._selected = None if self._selected == category_id else category_id
            self.update()
            self.picked.emit(self._selected)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows:
            painter.setPen(MUTED)
            painter.setFont(_font())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "这一期还没有支出。可以手工记一笔，或把导入的明细归类。")
            return
        name_w, text_w = 76, 190
        bar_left = name_w + 8
        bar_w = max(self.width() - bar_left - text_w - 12, 40)
        scale = max(max(row["amount"] for row in self._rows), max(self._budgets.values(), default=0), 1)
        for index, row in enumerate(self._rows):
            top = index * self.ROW
            if row["category_id"] == self._selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(SELECTED)
                painter.drawRoundedRect(QRectF(0, top + 2, self.width(), self.ROW - 4), 4, 4)
            color = self._colors.get(row["category"], GOLD)
            painter.setPen(BRIGHT)
            painter.setFont(_font(10))
            painter.drawText(QRectF(8, top, name_w, self.ROW), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, row["category"])
            bar_top = top + self.ROW / 2 - 6
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(TRACK)
            painter.drawRoundedRect(QRectF(bar_left, bar_top, bar_w, 12), 3, 3)
            width = bar_w * max(row["amount"], 0) / scale
            if width > 0:
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(bar_left, bar_top, max(width, 3), 12), 3, 3)
            budget = self._budgets.get(row["category_id"])
            over = False
            if budget:
                x = bar_left + bar_w * budget / scale
                over = row["amount"] > budget
                painter.setPen(QPen(UP if over else BRIGHT, 2))
                painter.drawLine(int(x), int(bar_top - 4), int(x), int(bar_top + 16))
            text = f"{row['amount']:,.2f}   {row['share'] * 100:.0f}%"
            painter.setPen(UP if over else BRIGHT)
            painter.setFont(_font(10))
            painter.drawText(
                QRectF(self.width() - text_w - 4, top, text_w, self.ROW / 2 + 4),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, text,
            )
            detail = f"{row['count']} 笔"
            if row["refund"]:
                detail += f" · 含退款 {row['refund']:,.2f}"
            if budget:
                detail += f" · 预算 {row['amount'] / budget * 100:.0f}%"
            painter.setPen(MUTED)
            painter.setFont(_font(8))
            painter.drawText(
                QRectF(self.width() - text_w - 4, top + self.ROW / 2 + 2, text_w, self.ROW / 2 - 2),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, detail,
            )


class StackedMonths(QWidget):
    """近几个月的支出，每月一根柱子，按类别分段。点柱子发出那个月份。"""

    picked = pyqtSignal(str)

    def __init__(self, empty_text="近几个月还没有支出"):
        super().__init__()
        self._keys, self._names, self._cells = [], [], {}
        self._colors = {}
        self._current = None
        self._empty = empty_text
        self.setMinimumHeight(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, keys, names, cells, colors, current=None):
        self._keys, self._names, self._cells = keys, names, cells
        self._colors = colors
        self._current = current
        self.update()

    def _slots(self):
        count = max(len(self._keys), 1)
        gap = 18
        width = max((self.width() - gap * (count + 1)) / count, 10)
        return gap, width

    def mousePressEvent(self, event):
        gap, width = self._slots()
        index = int((event.position().x() - gap / 2) // (width + gap))
        if 0 <= index < len(self._keys):
            self.picked.emit(self._keys[index])

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        totals = [sum(self._cells.get(key, {}).values()) for key in self._keys]
        if not self._keys or not any(totals):
            painter.setPen(MUTED)
            painter.setFont(_font())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty)
            return
        painter.setFont(_font(8))
        x = 0.0
        for name in self._names:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._colors.get(name, MUTED))
            painter.drawRoundedRect(QRectF(x, 5, 9, 9), 2, 2)
            painter.setPen(MUTED)
            label_w = painter.fontMetrics().horizontalAdvance(name) + 16
            painter.drawText(QRectF(x + 13, 0, label_w, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            x += label_w + 13
        gap, width = self._slots()
        base = self.height() - 26
        top = 44
        peak = max(totals) or 1
        for index, key in enumerate(self._keys):
            left = gap + index * (width + gap)
            if key == self._current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(SELECTED)
                painter.drawRoundedRect(QRectF(left - 6, top - 20, width + 12, base - top + 46), 4, 4)
            y = base
            cells = self._cells.get(key, {})
            for name in self._names:
                value = cells.get(name, 0)
                if value <= 0:
                    continue
                height = (base - top) * value / peak
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._colors.get(name, MUTED))
                painter.drawRect(QRectF(left, y - height, width, max(height, 1)))
                y -= height
            painter.setPen(MUTED)
            painter.setFont(_font(9))
            painter.drawText(QRectF(left - 10, base + 4, width + 20, 20), Qt.AlignmentFlag.AlignCenter, f"{int(key[5:7])} 月")
            if totals[index] > 0:
                painter.setPen(BRIGHT)
                painter.drawText(QRectF(left - 16, y - 19, width + 32, 16), Qt.AlignmentFlag.AlignCenter, f"{totals[index]:,.0f}")
