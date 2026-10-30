from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gxyfi import db
from gxyfi.dialogs import (
    edit_account,
    edit_balance,
    edit_budget,
    edit_category,
    edit_expense,
    edit_fx,
    edit_holding,
    edit_property,
)
from gxyfi.formatutil import fmt_cny, fmt_money, fmt_pct, fmt_price, fmt_signed, pnl_kind
from gxyfi.market import fetch_one, prepare_holding
from gxyfi.widgets import (
    FLAT,
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
            "储蓄看最近一次手工余额，投资看持仓乘以现价，不动产看现值减去贷款。右上角可以导出全部 Excel。本机账本不会自动上传到网站。",
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
            f"{len(summary['holdings'])} 笔持仓" + (f" · {summary['unpriced']} 笔无价格" if summary["unpriced"] else ""),
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
            self.hint.setText("余额按你手工录入的快照计算。投资价格优先用手工现价，没有时才用行情。")


class AccountsPage(Page):
    def __init__(self):
        super().__init__(
            "储蓄",
            "账户余额只来自你定时录入的快照，程序不会去银行取数。修改币种不会换算已经记下的数字。贷款如果记成负余额，不动产页就不要再填贷款。",
        )
        self.add_action("新增账户", self.add_account, "primary")
        self.add_action("编辑账户", self.edit_account)
        self.add_action("删除账户", self.delete_account, "danger")
        self.add_action("录入余额", self.add_balance, "primary")
        self.add_action("删除这条余额", self.delete_balance, "danger")
        self.table = make_table(["账户", "机构", "类型", "币种", "最近余额", "截止日期", "距今", "折合人民币"])
        self.history = make_table(["日期", "余额", "备注"])
        self.history.setMinimumHeight(160)
        self.table.itemSelectionChanged.connect(self.load_history)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_account())
        self.history.cellDoubleClicked.connect(lambda *_: self.edit_balance())
        splitter = QSplitter()
        splitter.addWidget(self.table)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 0, 0, 0)
        caption = QLabel("选中账户的余额记录")
        caption.setObjectName("PageSub")
        side_layout.addWidget(caption)
        side_layout.addWidget(self.history, 1)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.root.addWidget(splitter, 1)

    def reload(self):
        current = selected_id(self.table)
        summary = db.portfolio_summary()
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
        restore(self.table, current)
        if selected_id(self.table) is None and rows:
            self.table.selectRow(0)
        self.load_history()

    def load_history(self):
        account_id = selected_id(self.table)
        entries = db.list_balances(account_id) if account_id else []
        self.history.setRowCount(len(entries))
        for index, row in enumerate(entries):
            set_cell(self.history, index, 0, row["as_of_date"], row["id"])
            set_cell(self.history, index, 1, fmt_money(row["balance"]), align="right")
            set_cell(self.history, index, 2, row["note"])

    def add_account(self):
        data = edit_account(self)
        if data:
            db.save_account(data)
            self.reload()

    def edit_account(self):
        account_id = selected_id(self.table)
        if account_id is None:
            QMessageBox.information(self, "未选择", "请先选择一个账户。")
            return
        data = edit_account(self, db.get_account(account_id))
        if data:
            db.save_account(data, account_id)
            self.reload()

    def delete_account(self):
        account_id = selected_id(self.table)
        if account_id is None:
            QMessageBox.information(self, "未选择", "请先选择一个账户。")
            return
        if confirm(self, "删除账户会同时删除它的全部余额快照。"):
            db.delete_account(account_id)
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
            QMessageBox.information(self, "未选择", "请先在右侧选择一条余额。")
            return
        initial = next((row for row in db.list_balances(account_id) if row["id"] == entry_id), None)
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
            QMessageBox.information(self, "未选择", "请先在右侧选择一条余额。")
            return
        if confirm(self, "删除这条余额快照？"):
            db.delete_balance(entry_id)
            self.reload()


class HoldingsPage(Page):
    def __init__(self):
        super().__init__(
            "投资",
            "黄金按克、股票按股。填了手工现价就用手工现价；留空则用右上角刷新到的行情。Au(T+D) 的价格是人民币/克。",
        )
        self.add_action("新增持仓", self.add_holding, "primary")
        self.add_action("编辑", self.edit_holding)
        self.add_action("删除", self.delete_holding, "danger")
        self.table = make_table(["名称", "代码", "市场", "数量", "成本价", "现价", "涨跌", "市值¥", "浮盈¥", "来源"])
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_holding())
        self.root.addWidget(self.table, 1)

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
        super().__init__("支出", "支出按人民币登记，用来和年度预算对照。默认看本月。")
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
        self.total_label = QLabel("")
        self.total_label.setObjectName("TopMeta")
        self.toolbar.addWidget(self.total_label)
        self.table = make_table(["日期", "类别", "金额", "对象", "备注"])
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
            set_cell(self.table, index, 2, fmt_cny(row["amount"]), align="right")
            set_cell(self.table, index, 3, row["payee"])
            set_cell(self.table, index, 4, row["note"])
        self.total_label.setText(f"合计 {fmt_cny(sum(row['amount'] for row in rows))}")
        restore(self.table, current)

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
        data = edit_expense(self, db.list_categories(), initial)
        if data:
            db.save_expense(data, expense_id)
            self.reload()

    def delete_expense(self):
        expense_id = selected_id(self.table)
        if expense_id is None:
            QMessageBox.information(self, "未选择", "请先选择一笔支出。")
            return
        if confirm(self, "删除这笔支出？"):
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
