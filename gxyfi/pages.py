from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gxyfi import db
from gxyfi.dca import write_template
from gxyfi.dialogs import (
    edit_account,
    edit_balance,
    edit_budget,
    edit_category,
    edit_dca_plan,
    edit_dca_record,
    edit_expense,
    edit_fx,
    edit_holding,
    edit_property,
)
from gxyfi.formatutil import fmt_cny, fmt_money, fmt_pct, fmt_price, fmt_signed, pnl_kind
from gxyfi.market import fetch_one, prepare_holding
from gxyfi.classify import describe as describe_treat
from gxyfi.txn_ui import TXN_HEADERS, fill_txn_table, import_statement, undo_import
from gxyfi.txn_ui import edit_txn as edit_txn_dialog
from gxyfi.widgets import (
    DOWN,
    FLAT,
    GOLD,
    UP,
    MetricCard,
    MixBar,
    MonthBars,
    Page,
    Panel,
    make_table,
    pnl_color,
    selected_id,
    set_cell,
)

MARKET_LABEL = {"US": "美股", "A": "A股", "GOLD": "黄金", "OTHER": "其他"}


def confirm(parent, text):
    box = QMessageBox(parent)
    box.setWindowTitle("请确认")
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText("删除")
    box.button(QMessageBox.StandardButton.No).setText("取消")
    return box.exec() == QMessageBox.StandardButton.Yes


def restore(table, row_id):
    if row_id is None:
        return
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item and item.data(Qt.ItemDataRole.UserRole) == row_id:
            table.selectRow(row)
            return


class DashboardPage(Page):
    def __init__(self):
        super().__init__(
            "总览",
            "储蓄看最近一次余额（手工录入或由明细生成），投资看持仓乘以现价，不动产看现值减去贷款。右上角可以导出全部 Excel，或登录网页账号与手机、网页同步。",
        )
        self.cards = {}
        specs = [
            ("savings", "储蓄"),
            ("invest", "投资"),
            ("property", "不动产净值"),
            ("net", "净资产"),
            ("month", "本月支出"),
            ("budget", "本年预算"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for index, (key, label) in enumerate(specs):
            card = MetricCard(label)
            self.cards[key] = card
            grid.addWidget(card, index // 3, index % 3)
        self.root.addLayout(grid)
        left = Panel("资产结构")
        self.mix = MixBar()
        left.body.addWidget(self.mix)
        right = Panel("近六月支出")
        self.bars = MonthBars()
        right.body.addWidget(self.bars)
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        bottom.addWidget(left, 1)
        bottom.addWidget(right, 1)
        self.root.addLayout(bottom, 1)
        self.hint = QLabel("")
        self.hint.setObjectName("PageSub")
        self.hint.setWordWrap(True)
        self.root.addWidget(self.hint)

    def reload(self):
        summary = db.portfolio_summary()
        net = summary["savings_cny"] + summary["invest_cny"] + summary["property_equity_cny"]
        missing = []
        if summary["savings_missing"] or summary["invest_missing"]:
            missing.append("有外币还没有汇率")
        if summary["unpriced"]:
            missing.append(f"{summary['unpriced']} 笔持仓没有价格")
        if summary["property_missing"]:
            missing.append("有不动产未填现值")
        self.cards["savings"].set_value(
            fmt_cny(summary["savings_cny"]),
            f"{len(summary['accounts'])} 个账户" + (" · 有余额未折算" if summary["savings_missing"] else ""),
        )
        self.cards["invest"].set_value(
            fmt_cny(summary["invest_cny"]),
            f"{len(summary['holdings'])} 笔持仓 · {len(summary['dca_plans'])} 个定投"
            + (f" · {summary['unpriced']} 笔无价格" if summary["unpriced"] else ""),
        )
        self.cards["property"].set_value(
            fmt_cny(summary["property_equity_cny"]),
            f"登记月租金 {fmt_cny(summary['rent_monthly'])}",
        )
        self.cards["net"].set_value(fmt_cny(net), " · ".join(missing) if missing else "储蓄 + 投资 + 不动产净值")
        self.cards["month"].set_value(fmt_cny(summary["month_expense"]), summary["today"][:7])
        ratio = "尚未设置预算" if summary["budget_total"] == 0 else f"已用 {fmt_pct(summary['budget_ratio'])}"
        self.cards["budget"].set_value(fmt_cny(summary["budget_total"]), f"已支出 {fmt_cny(summary['year_expense'])} · {ratio}")
        self.mix.set_parts(
            [
                ("储蓄", summary["savings_cny"]),
                ("投资", summary["invest_cny"]),
                ("不动产", summary["property_equity_cny"]),
            ]
        )
        self.bars.set_rows(summary["monthly_expenses"])
        if summary["stale_accounts"]:
            names = "、".join(f"{item['name']}（{item['age']} 天）" for item in summary["stale_accounts"][:4])
            self.hint.setText(f"这些账户超过 {db.STALE_DAYS} 天没有更新余额：{names}")
        else:
            self.hint.setText("余额按你手工录入的快照计算。投资价格优先用手工现价，没有时才用行情。基金定投按确认份额计入投资。")


class AccountsPage(Page):
    def __init__(self):
        super().__init__(
            "储蓄",
            "余额可以手工录入，也可以导入银行、支付宝、微信导出的明细，导入后余额跟着明细自动更新。"
            "每个账户第一次导入时确认一次列的对应关系，以后同样格式的明细直接导入。",
        )
        self.add_action("新增账户", self.add_account, "primary")
        self.add_action("编辑账户", self.edit_account)
        self.add_action("删除账户", self.delete_account, "danger")
        self.toolbar.addSpacing(18)
        self.add_action("录入余额", self.add_balance)
        self.add_action("导入明细", self.import_txns, "primary")
        self.toolbar.addStretch(1)
        self.table = make_table(["账户", "机构", "类型", "币种", "最近余额", "截止日期", "距今", "折合人民币", "明细"])
        self.table.itemSelectionChanged.connect(self.load_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_account())

        self.history = make_table(["日期", "余额", "来源", "备注"])
        self.history.setMinimumHeight(160)
        self.history.cellDoubleClicked.connect(lambda *_: self.edit_balance())
        balance_tab = QWidget()
        balance_layout = QVBoxLayout(balance_tab)
        balance_layout.setContentsMargins(0, 10, 0, 0)
        balance_bar = QHBoxLayout()
        balance_bar.addWidget(self._note("「明细」来源的余额由交易明细自动生成，改明细后会自动更新。"), 1)
        balance_bar.addWidget(self._small("修改", self.edit_balance))
        balance_bar.addWidget(self._small("删除这条余额", self.delete_balance, "danger"))
        balance_layout.addLayout(balance_bar)
        balance_layout.addWidget(self.history, 1)

        self.txns = make_table([*TXN_HEADERS, "归类"])
        self.txns.setMinimumHeight(160)
        self.txns.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.txns.cellDoubleClicked.connect(lambda *_: self.edit_txn())
        txn_tab = QWidget()
        txn_layout = QVBoxLayout(txn_tab)
        txn_layout.setContentsMargins(0, 10, 0, 0)
        txn_bar = QHBoxLayout()
        self.txn_summary = self._note("")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索对方户名、摘要、备注")
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self.load_txns)
        txn_bar.addWidget(self.txn_summary, 1)
        txn_bar.addWidget(self.search)
        txn_bar.addWidget(self._small("补录一笔", self.add_txn))
        txn_bar.addWidget(self._small("修改", self.edit_txn))
        txn_bar.addWidget(self._small("删除所选", self.delete_txns, "danger"))
        txn_bar.addWidget(self._small("撤销一次导入", self.undo_import, "danger"))
        txn_layout.addLayout(txn_bar)
        txn_layout.addWidget(self.txns, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(balance_tab, "余额记录")
        self.tabs.addTab(txn_tab, "交易明细")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self.root.addWidget(splitter, 1)

    def _note(self, text):
        label = QLabel(text)
        label.setObjectName("PageSub")
        return label

    def _small(self, text, slot, kind="ghost"):
        button = QPushButton(text)
        button.setObjectName("DangerButton" if kind == "danger" else "GhostButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _account(self, quiet=False):
        account_id = selected_id(self.table)
        if account_id is None:
            if not quiet:
                QMessageBox.information(self, "未选择", "请先在上面选择一个账户。")
            return None
        return db.get_account(account_id)

    def reload(self):
        current = selected_id(self.table)
        summary = db.portfolio_summary()
        counts = db.txn_counts()
        rows = summary["accounts"]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            age = "尚未录入" if row["age"] is None else f"{row['age']} 天"
            age_color = UP if row["age"] is None or row["age"] > db.STALE_DAYS else FLAT
            set_cell(self.table, index, 0, row["name"], row["id"])
            set_cell(self.table, index, 1, row["institution"] or "—")
            set_cell(self.table, index, 2, row["kind"])
            set_cell(self.table, index, 3, row["currency"])
            set_cell(self.table, index, 4, "—" if row["balance"] is None else fmt_money(row["balance"]), align="right")
            set_cell(self.table, index, 5, row["as_of_date"] or "—")
            set_cell(self.table, index, 6, age, color=age_color)
            set_cell(self.table, index, 7, fmt_cny(row["cny"]), align="right")
            count = counts.get(row["id"])
            set_cell(self.table, index, 8, f"{count['n']} 笔 · 至 {count['last']}" if count else "—")
        restore(self.table, current)
        if selected_id(self.table) is None and rows:
            self.table.selectRow(0)
        self.load_detail()

    def load_detail(self):
        account_id = selected_id(self.table)
        entries = db.list_balances(account_id) if account_id else []
        self.history.setRowCount(len(entries))
        for index, row in enumerate(entries):
            source = row.get("source") or "手工"
            set_cell(self.history, index, 0, row["as_of_date"], row["id"])
            set_cell(self.history, index, 1, fmt_money(row["balance"]), align="right")
            set_cell(self.history, index, 2, source, color=GOLD if source == "明细" else None)
            set_cell(self.history, index, 3, row["note"])
        self.load_txns()

    def load_txns(self):
        account_id = selected_id(self.table)
        rows = db.list_txns(account_id) if account_id else []
        word = self.search.text().strip().lower()
        if word:
            rows = [row for row in rows if word in f"{row['counterparty']} {row['summary']} {row['note']}".lower()]
        fill_txn_table(self.txns, rows)
        names = {row["id"]: row["name"] for row in db.list_categories()}
        for index, row in enumerate(rows):
            label = describe_treat(row["treat"], row["category_id"], names)
            if not label and row["amount"] < 0:
                label = "待归类"
            set_cell(self.txns, index, len(TXN_HEADERS), label, color=GOLD if label == "待归类" else None)
        if not account_id:
            self.txn_summary.setText("")
        elif not rows:
            self.txn_summary.setText("没有找到明细" if word else "还没有明细。点上方「导入明细」，选银行或支付平台导出的 Excel / CSV。")
        else:
            income = sum(row["amount"] for row in rows if row["amount"] > 0)
            expense = -sum(row["amount"] for row in rows if row["amount"] < 0)
            self.txn_summary.setText(
                f"{len(rows)} 笔 · {rows[-1]['txn_date']} ~ {rows[0]['txn_date']} · 收入 {fmt_money(income)} · 支出 {fmt_money(expense)}"
            )
        self.tabs.setTabText(1, f"交易明细（{len(rows)}）" if rows else "交易明细")

    def add_account(self):
        data = edit_account(self)
        if data:
            db.save_account(data)
            self.reload()

    def edit_account(self):
        account = self._account()
        if account is None:
            return
        data = edit_account(self, account)
        if data:
            db.save_account(data, account["id"])
            self.reload()

    def delete_account(self):
        account = self._account()
        if account is None:
            return
        if confirm(self, f"删除「{account['name']}」会同时删除它的全部余额快照和交易明细。"):
            db.delete_account(account["id"])
            self.reload()

    def add_balance(self):
        data = edit_balance(self, db.list_accounts(), account_id=selected_id(self.table))
        if data:
            db.save_balance(data)
            self.reload()

    def edit_balance(self):
        entry_id = selected_id(self.history)
        account_id = selected_id(self.table)
        if entry_id is None or account_id is None:
            QMessageBox.information(self, "未选择", "请先在「余额记录」里选择一条余额。")
            return
        initial = next((row for row in db.list_balances(account_id) if row["id"] == entry_id), None)
        if initial and initial.get("source") == "明细":
            box = QMessageBox(self)
            box.setWindowTitle("这条余额来自明细")
            box.setText("这条余额是按交易明细算出来的。明细有错时，建议去「交易明细」里改那一笔，余额会自动重算。\n\n仍然要手工改这条余额吗？")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.button(QMessageBox.StandardButton.Yes).setText("手工修改")
            box.button(QMessageBox.StandardButton.No).setText("取消")
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        data = edit_balance(self, db.list_accounts(), initial)
        if not data:
            return
        try:
            db.save_balance(data, entry_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload()

    def delete_balance(self):
        entry_id = selected_id(self.history)
        if entry_id is None:
            QMessageBox.information(self, "未选择", "请先在「余额记录」里选择一条余额。")
            return
        if confirm(self, "删除这条余额快照？"):
            db.delete_balance(entry_id)
            self.reload()

    def import_txns(self):
        account = self._account()
        if account is None:
            return
        message = import_statement(self, account)
        if message:
            self.reload()
            self.tabs.setCurrentIndex(1)
            self.window().statusBar().showMessage(message)

    def _selected_txns(self):
        ids = []
        for index in self.txns.selectionModel().selectedRows():
            item = self.txns.item(index.row(), 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def add_txn(self):
        account = self._account()
        if account is None:
            return
        data = edit_txn_dialog(self)
        if data:
            db.save_txn({**data, "account_id": account["id"]})
            self.reload()
            self.tabs.setCurrentIndex(1)

    def edit_txn(self):
        ids = self._selected_txns()
        if len(ids) != 1:
            QMessageBox.information(self, "请选一笔", "请在「交易明细」里选中一笔再修改。")
            return
        initial = next((row for row in db.list_txns(selected_id(self.table)) if row["id"] == ids[0]), None)
        data = edit_txn_dialog(self, initial)
        if data:
            db.save_txn(data, ids[0])
            self.reload()

    def delete_txns(self):
        ids = self._selected_txns()
        if not ids:
            QMessageBox.information(self, "未选择", "请在「交易明细」里选中要删除的明细，可以按住 Ctrl 或 Shift 多选。")
            return
        if confirm(self, f"删除选中的 {len(ids)} 笔明细？余额会随之重算。\n以后再导入包含这几笔的明细时，它们会被重新导入。"):
            db.delete_txns(ids)
            self.reload()

    def undo_import(self):
        account = self._account()
        if account is not None and undo_import(self, account):
            self.reload()


class HoldingsPage(Page):
    def __init__(self):
        super().__init__(
            "投资",
            "股票和黄金记在上面。基金定投记在下面：先建计划，再记每一期确认。只知道当前份额时记一笔期初；要留下每期扣款，用 Excel 导入历史。同一只基金不要在两处各记一份份额。",
        )
        self.add_action("新增持仓", self.add_holding, "primary")
        self.add_action("编辑", self.edit_holding)
        self.add_action("删除", self.delete_holding, "danger")
        self.table = make_table(["名称", "代码", "市场", "数量", "成本价", "现价", "涨跌", "市值¥", "浮盈¥", "来源"])
        self.table.setMinimumHeight(160)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_holding())
        dca_box = QWidget()
        dca_layout = QVBoxLayout(dca_box)
        dca_layout.setContentsMargins(0, 8, 0, 0)
        dca_layout.setSpacing(8)
        heading = QLabel("基金定投")
        heading.setObjectName("Kicker")
        dca_layout.addWidget(heading)
        dca_bar = QHBoxLayout()
        dca_bar.setSpacing(8)
        for text, slot, kind in (
            ("新增计划", self.add_plan, "primary"),
            ("编辑计划", self.edit_plan, "ghost"),
            ("删除计划", self.delete_plan, "danger"),
            ("记一笔", self.add_record, "primary"),
            ("编辑记录", self.edit_record, "ghost"),
            ("删除记录", self.delete_record, "danger"),
            ("导入历史", self.import_records, "ghost"),
            ("导入模板", self.save_template, "ghost"),
        ):
            button = QPushButton(text)
            button.setObjectName({"primary": "PrimaryButton", "danger": "DangerButton"}.get(kind, "GhostButton"))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            dca_bar.addWidget(button)
        dca_bar.addStretch(1)
        dca_layout.addLayout(dca_bar)
        self.plans = make_table(["基金", "代码", "每期", "周期", "状态", "份额", "成本", "净值", "市值¥", "浮盈¥"])
        self.records = make_table(["日期", "类型", "金额", "净值", "份额", "手续费", "备注"])
        for table in (self.plans, self.records):
            table.setMinimumHeight(150)
        self.plans.itemSelectionChanged.connect(self._show_records)
        self.plans.cellDoubleClicked.connect(lambda *_: self.edit_plan())
        self.records.cellDoubleClicked.connect(lambda *_: self.edit_record())
        tables = QSplitter()
        tables.setChildrenCollapsible(False)
        tables.addWidget(self.plans)
        tables.addWidget(self.records)
        tables.setStretchFactor(0, 3)
        tables.setStretchFactor(1, 2)
        dca_layout.addWidget(tables, 1)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.table)
        splitter.addWidget(dca_box)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self.root.addWidget(splitter, 1)

    def _show_records(self):
        self._fill_records(None)

    def _fill_records(self, record_id):
        plan_id = selected_id(self.plans)
        rows = [] if plan_id is None else db.list_dca_records(plan_id)
        self.records.setRowCount(len(rows))
        for index, row in enumerate(rows):
            set_cell(self.records, index, 0, row["trade_date"], row["id"])
            set_cell(self.records, index, 1, row["kind"])
            set_cell(self.records, index, 2, fmt_cny(row["amount"]), align="right")
            set_cell(self.records, index, 3, fmt_price(row["nav"]), align="right")
            set_cell(self.records, index, 4, fmt_price(row["shares"]), align="right")
            set_cell(self.records, index, 5, fmt_cny(row["fee"]), align="right")
            set_cell(self.records, index, 6, row["note"])
        restore(self.records, record_id)

    def reload(self):
        current = selected_id(self.table)
        rows, _total, _missing = db.valued_holdings()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            set_cell(self.table, index, 0, row["name"], row["id"])
            set_cell(self.table, index, 1, row["symbol"])
            set_cell(self.table, index, 2, MARKET_LABEL.get(row["market"], row["market"]))
            set_cell(self.table, index, 3, fmt_price(row["quantity"]), align="right")
            set_cell(self.table, index, 4, fmt_price(row["avg_cost"]), align="right")
            set_cell(self.table, index, 5, fmt_price(row["price"]), align="right")
            set_cell(self.table, index, 6, fmt_pct(row["day"]), align="right", color=pnl_color(pnl_kind(row["day"])))
            set_cell(self.table, index, 7, fmt_cny(row["cny"]), align="right")
            set_cell(self.table, index, 8, fmt_cny(row["pnl_cny"]), align="right", color=pnl_color(pnl_kind(row["pnl_cny"])))
            set_cell(self.table, index, 9, row["source"] or "—")
        restore(self.table, current)
        plan_id = selected_id(self.plans)
        record_id = selected_id(self.records)
        plans, _total, _unpriced = db.dca_plan_rows()
        self.plans.blockSignals(True)
        self.plans.setRowCount(len(plans))
        for index, row in enumerate(plans):
            set_cell(self.plans, index, 0, row["name"], row["id"])
            set_cell(self.plans, index, 1, row["symbol"])
            set_cell(self.plans, index, 2, fmt_cny(row["amount"]), align="right")
            set_cell(self.plans, index, 3, row["cycle_label"])
            set_cell(self.plans, index, 4, row["status"])
            set_cell(self.plans, index, 5, fmt_price(row["shares"]), align="right")
            set_cell(self.plans, index, 6, fmt_cny(row["cost"]), align="right")
            set_cell(self.plans, index, 7, fmt_price(row["nav"]), align="right")
            set_cell(self.plans, index, 8, fmt_cny(row["market_value"]), align="right")
            set_cell(self.plans, index, 9, fmt_cny(row["pnl"]), align="right", color=pnl_color(pnl_kind(row["pnl"])))
        self.plans.blockSignals(False)
        restore(self.plans, plan_id)
        self._fill_records(record_id)

    def add_holding(self):
        self._save_dialog()

    def edit_holding(self):
        holding_id = selected_id(self.table)
        if holding_id is None:
            QMessageBox.information(self, "未选择", "请先选择一笔持仓。")
            return
        current = next((row for row in db.list_holdings() if row["id"] == holding_id), None)
        self._save_dialog(current, holding_id)

    def _save_dialog(self, initial=None, holding_id=None):
        data = edit_holding(self, initial)
        if not data:
            return
        try:
            db.save_holding(prepare_holding(data), holding_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload()

    def delete_holding(self):
        holding_id = selected_id(self.table)
        if holding_id is None:
            QMessageBox.information(self, "未选择", "请先选择一笔持仓。")
            return
        if confirm(self, "删除这笔持仓？行情缓存还会留着。"):
            db.delete_holding(holding_id)
            self.reload()

    def _selected_plan(self):
        plan_id = selected_id(self.plans)
        if plan_id is None:
            QMessageBox.information(self, "未选择", "请先选择一个定投计划。")
            return None
        return plan_id

    def add_plan(self):
        data = edit_dca_plan(self)
        if not data:
            return
        try:
            db.save_dca_plan(data)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload()

    def edit_plan(self):
        plan_id = self._selected_plan()
        if plan_id is None:
            return
        current = next((row for row in db.list_dca_plans() if row["id"] == plan_id), None)
        data = edit_dca_plan(self, current)
        if not data:
            return
        try:
            db.save_dca_plan(data, plan_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload()

    def delete_plan(self):
        plan_id = self._selected_plan()
        if plan_id is None:
            return
        if confirm(self, "删除这个定投计划？已经记下的确认记录会一起删除。"):
            db.delete_dca_plan(plan_id)
            self.reload()

    def add_record(self):
        plan_id = self._selected_plan()
        if plan_id is None:
            return
        data = edit_dca_record(self)
        if not data:
            return
        self._save_record(plan_id, data)

    def edit_record(self):
        plan_id = self._selected_plan()
        record_id = selected_id(self.records)
        if plan_id is None or record_id is None:
            if plan_id is not None:
                QMessageBox.information(self, "未选择", "请先选择一条确认记录。")
            return
        current = next((row for row in db.list_dca_records(plan_id) if row["id"] == record_id), None)
        data = edit_dca_record(self, current)
        if not data:
            return
        self._save_record(plan_id, data, record_id)

    def _save_record(self, plan_id, data, record_id=None):
        try:
            db.save_dca_record({**data, "plan_id": plan_id}, record_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload()

    def delete_record(self):
        record_id = selected_id(self.records)
        if record_id is None:
            QMessageBox.information(self, "未选择", "请先选择一条确认记录。")
            return
        if confirm(self, "删除这条确认记录？"):
            try:
                db.delete_dca_record(record_id)
            except ValueError as exc:
                QMessageBox.warning(self, "无法删除", str(exc))
                return
            self.reload()

    def import_records(self):
        plan_id = self._selected_plan()
        if plan_id is None:
            return
        path, _selected = QFileDialog.getOpenFileName(self, "导入定投历史", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            count = db.import_dca_records(plan_id, path)
        except ValueError as exc:
            QMessageBox.warning(self, "无法导入", str(exc))
            return
        QMessageBox.information(self, "已导入", f"追加了 {count} 条确认记录。")
        self.reload()

    def save_template(self):
        path, _selected = QFileDialog.getSaveFileName(self, "保存导入模板", "定投导入模板.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            write_template(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        QMessageBox.information(self, "模板已保存", path)


class PropertyPage(Page):
    def __init__(self):
        super().__init__(
            "不动产",
            "金额按人民币登记。净值 = 现值 − 贷款余额。没有填写现值的资产不计入总览。",
        )
        self.add_action("新增", self.add_property, "primary")
        self.add_action("编辑", self.edit_property)
        self.add_action("删除", self.delete_property, "danger")
        self.table = make_table(["名称", "类型", "城市", "面积", "购入价", "现值", "贷款", "净值", "月租金"])
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_property())
        self.root.addWidget(self.table, 1)

    def reload(self):
        current = selected_id(self.table)
        rows = db.portfolio_summary()["properties"]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            set_cell(self.table, index, 0, row["name"], row["id"])
            set_cell(self.table, index, 1, row["kind"])
            set_cell(self.table, index, 2, row["city"] or "—")
            set_cell(self.table, index, 3, "—" if row["area_sqm"] is None else fmt_money(row["area_sqm"]), align="right")
            set_cell(self.table, index, 4, fmt_cny(row["purchase_price"]), align="right")
            set_cell(self.table, index, 5, fmt_cny(row["current_value"]), align="right")
            set_cell(self.table, index, 6, fmt_cny(row["mortgage_balance"]), align="right")
            set_cell(self.table, index, 7, fmt_cny(row["equity"]), align="right")
            set_cell(self.table, index, 8, fmt_cny(row["monthly_rent"]), align="right")
        restore(self.table, current)

    def add_property(self):
        self._save()

    def edit_property(self):
        property_id = selected_id(self.table)
        if property_id is None:
            QMessageBox.information(self, "未选择", "请先选择一处不动产。")
            return
        current = next((row for row in db.list_properties() if row["id"] == property_id), None)
        self._save(current, property_id)

    def _save(self, initial=None, property_id=None):
        data = edit_property(self, initial)
        if data:
            db.save_property(data, property_id)
            self.reload()

    def delete_property(self):
        property_id = selected_id(self.table)
        if property_id is None:
            QMessageBox.information(self, "未选择", "请先选择一处不动产。")
            return
        if confirm(self, "删除这处不动产？"):
            db.delete_property(property_id)
            self.reload()


class ExpensesPage(Page):
    def __init__(self):
        super().__init__(
            "支出",
            "支出按人民币登记，用来和年度预算对照。默认看本月。可以手工记，也可以把导入的账户明细归类成支出；退款会冲减对应类别。",
        )
        self.month_box = QComboBox()
        self.month_box.addItem("全部月份", None)
        for year, month in reversed(db.recent_months(24)):
            key = f"{year:04d}-{month:02d}"
            self.month_box.addItem(key, key)
        current = self.month_box.findData(date.today().strftime("%Y-%m"))
        if current >= 0:
            self.month_box.setCurrentIndex(current)
        self.month_box.currentIndexChanged.connect(lambda *_: self.reload())
        self.toolbar.addWidget(self.month_box)
        self.add_action("记一笔", self.add_expense, "primary")
        self.add_action("编辑", self.edit_expense)
        self.add_action("删除", self.delete_expense, "danger")
        self.add_action("新类别", self.add_category)
        self.toolbar.addSpacing(18)
        self.classify_btn = self.add_action("归类明细", self.open_classify, "primary")
        self.total_label = QLabel("")
        self.total_label.setObjectName("TopMeta")
        self.toolbar.addWidget(self.total_label)
        self.toolbar.addStretch(1)
        self.table = make_table(["日期", "类别", "金额", "对象", "来源", "备注"])
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_expense())
        self.root.addWidget(self.table, 1)

    def _visible(self):
        key = self.month_box.currentData()
        rows = db.list_expenses()
        if key:
            rows = [row for row in rows if row["occurred_on"].startswith(key)]
        return rows

    def reload(self):
        current = selected_id(self.table)
        rows = self._visible()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            set_cell(self.table, index, 0, row["occurred_on"], row["id"])
            set_cell(self.table, index, 1, row["category"])
            set_cell(self.table, index, 2, fmt_cny(row["amount"]), align="right", color=DOWN if row["amount"] < 0 else None)
            set_cell(self.table, index, 3, row["payee"])
            set_cell(self.table, index, 4, self._source(row), color=GOLD if row.get("txn_id") else None)
            set_cell(self.table, index, 5, row["note"])
        self.total_label.setText(f"    合计 {fmt_cny(sum(row['amount'] for row in rows))}")
        restore(self.table, current)
        waiting = db.pending_count()
        self.classify_btn.setText(f"归类明细（{waiting} 笔待归类）" if waiting else "归类明细")

    @staticmethod
    def _source(row):
        if not row.get("txn_id"):
            return "手工"
        if (row.get("source") or "手工") == "手工":
            return "手工 · 已对上明细"
        return "明细 · 退款" if row["amount"] < 0 else "明细"

    def open_classify(self):
        from gxyfi.classify_ui import open_classify

        if open_classify(self):
            self.reload()

    def add_expense(self):
        data = edit_expense(self, db.list_categories())
        if data:
            db.save_expense(data)
            self.reload()

    def edit_expense(self):
        expense_id = selected_id(self.table)
        if expense_id is None:
            QMessageBox.information(self, "未选择", "请先选择一笔支出。")
            return
        initial = next((row for row in db.list_expenses() if row["id"] == expense_id), None)
        if initial and initial["amount"] < 0:
            QMessageBox.information(self, "这是一笔退款", "退款来自账户明细。要改类别，请在「归类明细 → 已归类」里改；要改金额，请改那笔明细。")
            return
        data = edit_expense(self, db.list_categories(), initial)
        if data:
            db.save_expense(data, expense_id)
            self.reload()

    def delete_expense(self):
        expense_id = selected_id(self.table)
        if expense_id is None:
            QMessageBox.information(self, "未选择", "请先选择一笔支出。")
            return
        linked = next((row for row in db.list_expenses() if row["id"] == expense_id), {}).get("txn_id")
        text = "删除这笔支出？" + ("\n它对应的那笔明细会标成「不算支出」，明细本身不删。" if linked else "")
        if confirm(self, text):
            db.delete_expense(expense_id)
            self.reload()

    def add_category(self):
        name = edit_category(self)
        if not name:
            return
        try:
            db.save_category(name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.reload()


class BudgetPage(Page):
    def __init__(self):
        super().__init__("预算", "按日历年、按支出类别设置。执行率 = 本年该类已支出 ÷ 年度预算。")
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year)
        self.year.valueChanged.connect(lambda *_: self.reload())
        self.toolbar.addWidget(self.year)
        self.add_action("设置预算", self.edit_line, "primary")
        self.add_action("复制上一年", self.copy_previous)
        self.summary = QLabel("")
        self.summary.setObjectName("TopMeta")
        self.toolbar.addWidget(self.summary)
        self.table = make_table(["类别", "年度预算", "已支出", "剩余", "执行率"])
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_line())
        self.root.addWidget(self.table, 1)

    def reload(self):
        current = selected_id(self.table)
        rows = db.budget_progress(self.year.value())
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            color = UP if row["remain"] < 0 else FLAT
            set_cell(self.table, index, 0, row["category"], row["category_id"])
            set_cell(self.table, index, 1, fmt_cny(row["planned"]), align="right")
            set_cell(self.table, index, 2, fmt_cny(row["actual"]), align="right")
            set_cell(self.table, index, 3, fmt_signed(row["remain"]), align="right", color=color)
            set_cell(self.table, index, 4, fmt_pct(row["ratio"]), align="right", color=color)
        planned = sum(row["planned"] for row in rows)
        actual = sum(row["actual"] for row in rows)
        ratio = "—" if planned == 0 else fmt_pct(actual / planned)
        self.summary.setText(f"预算 {fmt_cny(planned)} · 已支出 {fmt_cny(actual)} · {ratio}")
        restore(self.table, current)

    def edit_line(self):
        category_id = selected_id(self.table)
        if category_id is None:
            QMessageBox.information(self, "未选择", "请先选择一个类别。")
            return
        row = next(item for item in db.budget_progress(self.year.value()) if item["category_id"] == category_id)
        data = edit_budget(self, row["category"], row["planned"], row["note"])
        if data:
            db.save_budget(self.year.value(), category_id, data["amount"], data["note"])
            self.reload()

    def copy_previous(self):
        year = self.year.value()
        box = QMessageBox(self)
        box.setWindowTitle("复制预算")
        box.setText(f"用 {year - 1} 年的预算覆盖 {year} 年？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("覆盖")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            count = db.copy_budget(year - 1, year)
        except ValueError as exc:
            QMessageBox.information(self, "没有可复制的预算", str(exc))
            return
        self.reload()
        QMessageBox.information(self, "已复制", f"写入了 {count} 个类别。")


class _LookupWorker(QThread):
    finished_payload = pyqtSignal(object)

    def __init__(self, market, symbol, token):
        super().__init__()
        self.market = market
        self.symbol = symbol
        self.token = token

    def run(self):
        self.finished_payload.emit(fetch_one(self.market, self.symbol, self.token))


class MarketPage(Page):
    def __init__(self):
        super().__init__(
            "行情",
            "只查询你持仓里的代码。黄金来自上金所延时行情，A 股先用新浪、失败再用东方财富，美股默认用 Yahoo Finance。这些来源都没有服务等级承诺，所以最后一次成功的价格会留在本机。",
        )
        self.add_action("汇率与 Token", self.edit_settings, "primary")
        self.meta = QLabel("")
        self.meta.setObjectName("TopMeta")
        self.meta.setWordWrap(True)
        self.toolbar.addWidget(self.meta, 1)
        lookup = QHBoxLayout()
        self.lookup_market = QComboBox()
        self.lookup_market.addItem("黄金", "GOLD")
        self.lookup_market.addItem("A股", "A")
        self.lookup_market.addItem("美股", "US")
        self.lookup_symbol = QLineEdit()
        self.lookup_symbol.setPlaceholderText("Au(T+D) / 600519 / AAPL")
        self.lookup_button = self._button("查询这个代码")
        self.lookup_result = QLabel("查询结果会显示在这里，不会自动变成持仓。")
        self.lookup_result.setObjectName("PageSub")
        self.lookup_result.setWordWrap(True)
        lookup.addWidget(self.lookup_market)
        lookup.addWidget(self.lookup_symbol, 1)
        lookup.addWidget(self.lookup_button)
        self.root.addLayout(lookup)
        self.root.addWidget(self.lookup_result)
        self.table = make_table(["市场", "代码", "名称", "价格", "涨跌", "行情时间", "来源", "状态"])
        self.root.addWidget(self.table, 1)
        self._worker = None

    def _button(self, text):
        from PyQt6.QtWidgets import QPushButton

        button = QPushButton(text)
        button.setObjectName("GhostButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.lookup)
        return button

    def reload(self):
        fx = db.fx_rates()
        token = "已保存 Finnhub Token" if db.get_setting("finnhub_token") else "美股未设置 Finnhub，使用 Yahoo Finance"

        def rate_text(code):
            item = fx[code]
            if item["rate"] is None:
                return f"{code} 未设置"
            return f"{code} {item['rate']:.4f}（{item['source']}）"

        self.meta.setText(f"{rate_text('USD')}    {rate_text('HKD')}    {token}")
        rows = db.list_quotes()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            day = None
            if row["price"] is not None and row["prev_close"] not in (None, 0):
                day = (row["price"] - row["prev_close"]) / row["prev_close"]
            set_cell(self.table, index, 0, MARKET_LABEL.get(row["market"], row["market"]))
            set_cell(self.table, index, 1, row["symbol"])
            set_cell(self.table, index, 2, row["name"] or "—")
            set_cell(self.table, index, 3, "—" if row["price"] is None else fmt_money(row["price"], 4), align="right")
            set_cell(self.table, index, 4, fmt_pct(day), align="right", color=pnl_color(pnl_kind(day)))
            set_cell(self.table, index, 5, row["quoted_at"] or row["fetched_at"])
            set_cell(self.table, index, 6, row["source"] or "—")
            state = "成功" if row["ok"] else (row["message"] or "失败")
            set_cell(self.table, index, 7, state, color=FLAT if row["ok"] else UP)

    def edit_settings(self):
        result = edit_fx(self, db.fx_rates())
        if not result:
            return
        updates = {}
        for code in ("USD", "HKD"):
            rate = result[code]
            if rate is None:
                updates[code] = {"rate": None, "source": "未设置", "at": ""}
            else:
                updates[code] = {"rate": rate, "source": "手工", "at": db.now_text()}
        db.save_fx_rates(updates)
        if result["clear_token"]:
            db.set_setting("finnhub_token", "")
        elif result["token"]:
            db.set_setting("finnhub_token", result["token"])
        self.reload()

    def lookup(self):
        if self._worker and self._worker.isRunning():
            return
        symbol = self.lookup_symbol.text().strip()
        if not symbol:
            QMessageBox.information(self, "缺少代码", "请先填写代码。")
            return
        self.lookup_button.setEnabled(False)
        self.lookup_result.setText("正在查询…")
        self._worker = _LookupWorker(self.lookup_market.currentData(), symbol, db.get_setting("finnhub_token") or "")
        self._worker.finished_payload.connect(self._lookup_done)
        self._worker.start()

    def _lookup_done(self, quote):
        self.lookup_button.setEnabled(True)
        if quote.get("ok"):
            db.save_quotes([quote])
            self.lookup_result.setText(
                f"{quote['name'] or quote['symbol']}  {fmt_money(quote['price'], 4)} {quote['currency']}  ·  {quote['source']}  ·  {quote['quoted_at']}"
            )
            self.reload()
        else:
            self.lookup_result.setText(quote.get("message") or "没有查到价格")
