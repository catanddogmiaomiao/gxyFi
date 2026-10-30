from __future__ import annotations

from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from gxyfi import db
from gxyfi.export_excel import export_workbook
from gxyfi.market import fetch_refresh
from gxyfi.pages import (
    AccountsPage,
    BudgetPage,
    DashboardPage,
    ExpensesPage,
    HoldingsPage,
    MarketPage,
    PropertyPage,
)


class RefreshWorker(QThread):
    finished_payload = pyqtSignal(object)

    def __init__(self, holdings, token):
        super().__init__()
        self.holdings = holdings
        self.token = token

    def run(self):
        try:
            payload = fetch_refresh(self.holdings, self.token)
        except Exception as exc:
            payload = {"quotes": [], "fx_updates": {}, "errors": [str(exc)]}
        self.finished_payload.emit(payload)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("gxyFi")
        self.resize(1360, 860)
        self.setMinimumSize(1180, 720)
        self._worker = None

        shell = QWidget()
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())

        right = QWidget()
        right.setObjectName("Page")
        right.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        line = QFrame()
        line.setObjectName("GoldLine")
        line.setFixedHeight(2)
        line.setFrameShape(QFrame.Shape.NoFrame)
        column.addWidget(line)
        column.addWidget(self._topbar())
        self.stack = QStackedWidget()
        self.pages = [
            DashboardPage(),
            AccountsPage(),
            HoldingsPage(),
            PropertyPage(),
            ExpensesPage(),
            BudgetPage(),
            MarketPage(),
        ]
        for page in self.pages:
            self.stack.addWidget(page)
        column.addWidget(self.stack, 1)
        root.addWidget(right, 1)
        self.setCentralWidget(shell)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status = status
        permanent = QLabel("仅保存在这台电脑")
        permanent.setObjectName("TopMeta")
        status.addPermanentWidget(permanent)
        status.showMessage("行情只用于估值，有延迟，不构成投资建议")
        self.show_page(0)

    def _sidebar(self):
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 28, 0, 16)
        layout.setSpacing(2)
        brand_wrap = QVBoxLayout()
        brand_wrap.setContentsMargins(22, 0, 18, 18)
        brand = QLabel("gxyFi")
        brand.setObjectName("Brand")
        sub = QLabel("家庭资产台账")
        sub.setObjectName("BrandSub")
        brand_wrap.addWidget(brand)
        brand_wrap.addWidget(sub)
        layout.addLayout(brand_wrap)
        self.nav_buttons = []
        labels = ["总览", "储蓄", "投资", "不动产", "支出", "预算", "行情"]
        for index, label in enumerate(labels):
            button = QPushButton(f"{index + 1:02d}    {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self.show_page(i))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        layout.addStretch(1)
        foot = QLabel("手工记账\n行情只用于估值")
        foot.setObjectName("SideFoot")
        layout.addWidget(foot)
        return side

    def _topbar(self):
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 14, 28, 0)
        self.fx_label = QLabel("")
        self.fx_label.setObjectName("TopMeta")
        layout.addWidget(self.fx_label, 1)
        self.refresh_btn = QPushButton("刷新行情")
        self.refresh_btn.setObjectName("GhostButton")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh_quotes)
        export_btn = QPushButton("导出 Excel")
        export_btn.setObjectName("PrimaryButton")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self.export_excel)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(export_btn)
        return bar

    def show_page(self, index):
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
        self.stack.setCurrentIndex(index)
        self.pages[index].reload()
        self._refresh_fx_label()

    def _refresh_fx_label(self):
        fx = db.fx_rates()
        usd = fx["USD"]
        if usd["rate"] is None:
            self.fx_label.setText("美元汇率未设置 · 外币持仓暂不折合人民币")
            return
        self.fx_label.setText(f"USD/CNY {usd['rate']:.4f} · {usd['source']}")

    def refresh_quotes(self):
        if self._worker and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("刷新中…")
        self._status.showMessage("正在获取黄金、A 股、美股和汇率…")
        self._worker = RefreshWorker(db.list_holdings(), db.get_setting("finnhub_token") or "")
        self._worker.finished_payload.connect(self._quotes_ready)
        self._worker.start()

    def _quotes_ready(self, payload):
        db.save_quotes(payload.get("quotes") or [])
        if payload.get("fx_updates"):
            db.save_fx_rates(payload["fx_updates"])
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新行情")
        quotes = payload.get("quotes") or []
        ok = sum(1 for item in quotes if item.get("ok"))
        failed = [item["symbol"] for item in quotes if not item.get("ok")]
        message = f"行情已更新 {ok}/{len(quotes)}。"
        if failed:
            message += " 失败：" + "、".join(failed[:5]) + "。"
        if payload.get("errors"):
            message += " 汇率：" + "；".join(payload["errors"][:2]) + "。"
        message += " 仅供估值，有延迟，不构成投资建议。"
        self._status.showMessage(message)
        self.show_page(self.stack.currentIndex())

    def export_excel(self):
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home()
        default = str(desktop / f"gxyFi-台账-{date.today():%Y%m%d}.xlsx")
        path, _selected = QFileDialog.getSaveFileName(self, "导出 Excel", default, "Excel 工作簿 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_workbook(path)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "已导出", path)
        self._status.showMessage(f"已导出 {path}")
