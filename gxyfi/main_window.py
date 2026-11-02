from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
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

from gxyfi import cloud, db
from gxyfi.sync_ui import SyncDialog
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


class CloudWorker(QThread):
    """在后台向网页端取整本账（或让网页端刷新行情后取回）。"""

    done = pyqtSignal(object, object)

    def __init__(self, quotes=False):
        super().__init__()
        self.quotes = quotes
        self.cfg = cloud.config()
        self.writes = cloud.WRITES

    def run(self):
        try:
            state = cloud.fetch_quotes(self.cfg) if self.quotes else cloud.fetch_state(self.cfg)
        except Exception as exc:
            self.done.emit(None, exc)
            return
        self.done.emit(state, None)


PULL_EVERY_MS = 60_000
PULL_ON_FOCUS_AFTER = 15


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("gxyFi")
        self.resize(1360, 860)
        self.setMinimumSize(1180, 720)
        self._worker = None
        self._pull_worker = None
        self._last_pull = 0.0
        self._offline = False

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
        self.cloud_label = QLabel("")
        self.cloud_label.setObjectName("TopMeta")
        status.addPermanentWidget(self.cloud_label)
        status.showMessage("行情只用于估值，有延迟，不构成投资建议")
        self.show_page(0)
        self._refresh_cloud_label()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.pull)
        self._timer.start(PULL_EVERY_MS)
        QTimer.singleShot(300, self.pull)

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
        self.sync_btn = QPushButton("网页同步")
        self.sync_btn.setObjectName("GhostButton")
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.clicked.connect(self.open_sync)
        layout.addWidget(self.sync_btn)
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

    def _refresh_cloud_label(self):
        cfg = cloud.config()
        if not cloud.enabled():
            self.cloud_label.setText("仅保存在这台电脑")
            self.sync_btn.setText("网页同步")
        elif cloud.expired():
            self.cloud_label.setText(f"网页账号 {cfg['user']} · 登录已过期，只能查看")
            self.sync_btn.setText("重新登录")
        elif self._offline:
            self.cloud_label.setText(f"网页账号 {cfg['user']} · 离线，显示 {cfg['synced_at']} 的内容")
            self.sync_btn.setText("网页同步 · 离线")
        else:
            self.cloud_label.setText(f"网页账号 {cfg['user']} · 已同步 {cfg['synced_at']}")
            self.sync_btn.setText("网页同步 · 已连接")

    def _reload(self):
        self.show_page(self.stack.currentIndex())
        self._refresh_cloud_label()

    def open_sync(self):
        dialog = SyncDialog(self, self.pull_now)
        dialog.exec()
        self._offline = False
        self._reload()
        if dialog.changed:
            self._status.showMessage(cloud.config()["user"] and "已切换到网页账号" or "已退出网页账号，这台电脑上的缓存已清空")

    def pull_now(self):
        self.pull(force=True)

    def pull(self, force=False):
        if not cloud.enabled() or cloud.expired():
            return
        if self._pull_worker and self._pull_worker.isRunning():
            return
        if not force and QApplication.activeModalWidget() is not None:
            return
        self._last_pull = time.monotonic()
        if force:
            self._status.showMessage("正在从网页端同步…")
        self._pull_worker = CloudWorker()
        self._pull_worker.done.connect(lambda state, error: self._pulled(state, error, force))
        self._pull_worker.start()

    def _pulled(self, state, error, loud):
        worker = self._pull_worker
        if error is not None:
            if isinstance(error, cloud.AuthExpired):
                cloud.mark_expired()
                self._status.showMessage("网页端登录已过期，请点右上角「重新登录」。")
            else:
                self._offline = True
                if loud:
                    self._status.showMessage(f"同步失败：{error}")
            self._refresh_cloud_label()
            return
        self._offline = False
        if worker.writes != cloud.WRITES or worker.cfg["user"] != cloud.config()["user"] or not cloud.enabled():
            return
        if QApplication.activeModalWidget() is not None and not loud:
            self._refresh_cloud_label()
            return
        cloud.apply(state)
        self._reload()
        if loud:
            self._status.showMessage("已从网页端同步")

    def changeEvent(self, event):
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.ActivationChange
            and self.isActiveWindow()
            and time.monotonic() - self._last_pull > PULL_ON_FOCUS_AFTER
        ):
            self.pull()

    def refresh_quotes(self):
        if self._worker and self._worker.isRunning():
            return
        if cloud.enabled():
            if cloud.expired():
                QMessageBox.warning(self, "不能刷新", "网页端登录已过期，请点右上角「重新登录」。")
                return
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("刷新中…")
            self._status.showMessage("网页端正在获取黄金、A 股、美股和汇率…")
            self._worker = CloudWorker(quotes=True)
            self._worker.done.connect(self._cloud_quotes_ready)
            self._worker.start()
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

    def _cloud_quotes_ready(self, state, error):
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新行情")
        if error is not None:
            if isinstance(error, cloud.AuthExpired):
                cloud.mark_expired()
            self._status.showMessage(f"行情没有刷新：{error}")
            self._refresh_cloud_label()
            return
        if not cloud.enabled():
            return
        problems = state.pop("quoteErrors", None) or []
        cloud.apply(state)
        self._offline = False
        self._reload()
        message = "网页端已刷新行情。"
        if problems:
            message += " 未取到：" + "；".join(str(item) for item in problems[:4]) + "。"
        self._status.showMessage(message + " 仅供估值，有延迟，不构成投资建议。")

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
