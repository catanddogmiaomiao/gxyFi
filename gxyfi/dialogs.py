from __future__ import annotations

from datetime import date, datetime

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gxyfi.dca import CYCLES, STATUSES, STOP_REASONS, STOPPED, WEEKDAYS, status_label
from gxyfi.db import ACCOUNT_KINDS, CURRENCIES, PROPERTY_KINDS


def _parse_number(text, label, required=False, allow_negative=False, allow_zero=True):
    raw = text.strip().replace(",", "").replace("，", "")
    if not raw:
        if required:
            return None, f"请填写{label}"
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, f"{label}不是有效数字"
    if not allow_negative and value < 0:
        return None, f"{label}不能为负数"
    if not allow_zero and value == 0:
        return None, f"{label}需要大于 0"
    return value, None


def _parse_optional_date(text, label):
    raw = text.strip()
    if not raw:
        return None, None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None, f"{label}请写成 YYYY-MM-DD"
    return raw, None


def _date_edit(value=None):
    widget = QDateEdit()
    widget.setCalendarPopup(True)
    widget.setDisplayFormat("yyyy-MM-dd")
    if value:
        widget.setDate(QDate.fromString(value, "yyyy-MM-dd"))
    else:
        widget.setDate(QDate.currentDate())
    return widget


def _combo(options, current=None):
    widget = QComboBox()
    for label, data in options:
        widget.addItem(label, data)
    if current is not None:
        index = widget.findData(current)
        if index >= 0:
            widget.setCurrentIndex(index)
    return widget


def _line(text="", placeholder=""):
    widget = QLineEdit("" if text is None else str(text))
    widget.setPlaceholderText(placeholder)
    return widget


def run_form(parent, title, rows, validate):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(460)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(16)
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(12)
    for label, widget in rows:
        form.addRow(label, widget)
    layout.addLayout(form)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("取消")
    cancel.setObjectName("GhostButton")
    ok = QPushButton("保存")
    ok.setObjectName("PrimaryButton")
    ok.setDefault(True)

    def try_accept():
        error = validate()
        if error:
            QMessageBox.warning(dialog, "无法保存", error)
            return
        dialog.accept()

    ok.clicked.connect(try_accept)
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return True


def edit_account(parent, initial=None):
    initial = initial or {}
    name = _line(initial.get("name", ""), "例如 工资卡")
    institution = _line(initial.get("institution", ""), "银行或券商")
    kind = _combo([(item, item) for item in ACCOUNT_KINDS], initial.get("kind", "活期"))
    currency = _combo([(item, item) for item in CURRENCIES], initial.get("currency", "CNY"))
    note = _line(initial.get("note", ""))
    rows = [("名称", name), ("机构", institution), ("类型", kind), ("币种", currency), ("备注", note)]

    def validate():
        if not name.text().strip():
            return "请填写账户名称"
        return None

    if not run_form(parent, "账户", rows, validate):
        return None
    return {
        "name": name.text().strip(),
        "institution": institution.text().strip(),
        "kind": kind.currentData(),
        "currency": currency.currentData(),
        "note": note.text().strip(),
    }


def edit_balance(parent, accounts, initial=None, account_id=None):
    initial = initial or {}
    if not accounts:
        QMessageBox.information(parent, "还没有账户", "请先新增一个账户，再录入余额。")
        return None
    account = _combo([(item["name"], item["id"]) for item in accounts], initial.get("account_id", account_id))
    as_of = _date_edit(initial.get("as_of_date"))
    balance = _line("" if initial.get("balance") is None else initial.get("balance"), "可为负数，表示透支或贷款")
    note = _line(initial.get("note", ""), "例如 月末对账")
    hint = QLabel("同一账户同一天再次保存，会覆盖那天的余额。")
    hint.setObjectName("PageSub")
    hint.setWordWrap(True)
    rows = [("账户", account), ("日期", as_of), ("余额", balance), ("备注", note), ("", hint)]

    def validate():
        value, error = _parse_number(balance.text(), "余额", required=True, allow_negative=True)
        if error:
            return error
        balance.setProperty("parsed", value)
        return None

    if not run_form(parent, "余额快照", rows, validate):
        return None
    return {
        "account_id": account.currentData(),
        "as_of_date": as_of.date().toString("yyyy-MM-dd"),
        "balance": balance.property("parsed"),
        "note": note.text().strip(),
    }


def edit_holding(parent, initial=None):
    initial = initial or {}
    markets = [("美股", "US"), ("A股", "A"), ("黄金", "GOLD"), ("其他", "OTHER")]
    name = _line(initial.get("name", ""), "可留空，将使用代码")
    symbol = _line(initial.get("symbol", ""))
    market = _combo(markets, initial.get("market", "GOLD"))
    quantity = _line("" if initial.get("quantity") is None else initial.get("quantity"), "黄金按克，股票按股")
    avg_cost = _line("" if initial.get("avg_cost") is None else initial.get("avg_cost"), "不知道可留空")
    manual = _line("" if initial.get("manual_price") is None else initial.get("manual_price"), "留空则使用行情")
    currency = _combo([(item, item) for item in CURRENCIES], initial.get("currency", "CNY"))
    account_label = _line(initial.get("account_label", ""), "例如 华泰证券，只是备注")
    note = _line(initial.get("note", ""))

    def sync_market():
        current = market.currentData()
        placeholders = {
            "US": "AAPL 或 BRK.B",
            "A": "600519",
            "GOLD": "Au(T+D)",
            "OTHER": "自定义代码",
        }
        symbol.setPlaceholderText(placeholders[current])
        currency.setEnabled(current == "OTHER")
        if current == "US":
            currency.setCurrentIndex(currency.findData("USD"))
        elif current in ("A", "GOLD"):
            currency.setCurrentIndex(currency.findData("CNY"))

    market.currentIndexChanged.connect(lambda *_: sync_market())
    sync_market()
    rows = [
        ("市场", market),
        ("代码", symbol),
        ("名称", name),
        ("数量", quantity),
        ("成本价", avg_cost),
        ("手工现价", manual),
        ("币种", currency),
        ("所在账户", account_label),
        ("备注", note),
    ]

    def validate():
        if not symbol.text().strip():
            return "请填写代码"
        qty, error = _parse_number(quantity.text(), "数量", required=True, allow_zero=False)
        if error:
            return error
        cost, error = _parse_number(avg_cost.text(), "成本价")
        if error:
            return error
        price, error = _parse_number(manual.text(), "手工现价")
        if error:
            return error
        quantity.setProperty("parsed", qty)
        avg_cost.setProperty("parsed", cost)
        manual.setProperty("parsed", price)
        return None

    if not run_form(parent, "持仓", rows, validate):
        return None
    return {
        "name": name.text().strip(),
        "symbol": symbol.text().strip(),
        "market": market.currentData(),
        "quantity": quantity.property("parsed"),
        "avg_cost": avg_cost.property("parsed"),
        "manual_price": manual.property("parsed"),
        "currency": currency.currentData(),
        "account_label": account_label.text().strip(),
        "note": note.text().strip(),
    }


def edit_property(parent, initial=None):
    initial = initial or {}
    name = _line(initial.get("name", ""))
    kind = _combo([(item, item) for item in PROPERTY_KINDS], initial.get("kind", "住宅"))
    city = _line(initial.get("city", ""))
    address = _line(initial.get("address", ""))
    area = _line("" if initial.get("area_sqm") is None else initial.get("area_sqm"), "平方米，可留空")
    acquired = _line(initial.get("acquired_on") or "", "YYYY-MM-DD，可留空")
    purchase = _line("" if initial.get("purchase_price") is None else initial.get("purchase_price"), "人民币，可留空")
    current = _line("" if initial.get("current_value") is None else initial.get("current_value"), "人民币，用于计算净值")
    valued = _line(initial.get("valued_on") or "", "留空则在填写现值时记为今天")
    mortgage = _line(0 if initial.get("mortgage_balance") is None else initial.get("mortgage_balance"), "没有贷款就填 0")
    rent = _line(0 if initial.get("monthly_rent") is None else initial.get("monthly_rent"), "没有出租就填 0")
    note = _line(initial.get("note", ""))
    hint = QLabel("金额按人民币登记。贷款若已在储蓄账户记为负数，这里请填 0，避免净资产被减两次。")
    hint.setWordWrap(True)
    hint.setObjectName("PageSub")
    rows = [
        ("名称", name),
        ("类型", kind),
        ("城市", city),
        ("地址", address),
        ("面积", area),
        ("购入日", acquired),
        ("购入价", purchase),
        ("现值", current),
        ("估值日", valued),
        ("贷款余额", mortgage),
        ("月租金", rent),
        ("备注", note),
        ("", hint),
    ]

    def validate():
        if not name.text().strip():
            return "请填写名称"
        parsed = {}
        for widget, label, kwargs in (
            (area, "面积", {}),
            (purchase, "购入价", {}),
            (current, "现值", {}),
            (mortgage, "贷款余额", {}),
            (rent, "月租金", {}),
        ):
            value, error = _parse_number(widget.text(), label, **kwargs)
            if error:
                return error
            parsed[label] = value
        acquired_on, error = _parse_optional_date(acquired.text(), "购入日")
        if error:
            return error
        valued_on, error = _parse_optional_date(valued.text(), "估值日")
        if error:
            return error
        name.setProperty("parsed_numbers", parsed)
        name.setProperty("acquired_on", acquired_on)
        name.setProperty("valued_on", valued_on)
        return None

    if not run_form(parent, "不动产", rows, validate):
        return None
    parsed = name.property("parsed_numbers")
    return {
        "name": name.text().strip(),
        "kind": kind.currentData(),
        "city": city.text().strip(),
        "address": address.text().strip(),
        "area_sqm": parsed["面积"],
        "acquired_on": name.property("acquired_on"),
        "purchase_price": parsed["购入价"],
        "current_value": parsed["现值"],
        "valued_on": name.property("valued_on"),
        "mortgage_balance": parsed["贷款余额"] or 0,
        "monthly_rent": parsed["月租金"] or 0,
        "note": note.text().strip(),
    }


def edit_expense(parent, categories, initial=None):
    initial = initial or {}
    category = _combo([(item["name"], item["id"]) for item in categories], initial.get("category_id"))
    occurred = _date_edit(initial.get("occurred_on"))
    amount = _line("" if initial.get("amount") is None else initial.get("amount"))
    payee = _line(initial.get("payee", ""), "商户或对象，可留空")
    note = _line(initial.get("note", ""))
    rows = [("日期", occurred), ("类别", category), ("金额", amount), ("对象", payee), ("备注", note)]

    def validate():
        value, error = _parse_number(amount.text(), "金额", required=True, allow_zero=False)
        if error:
            return error
        amount.setProperty("parsed", value)
        return None

    if not run_form(parent, "支出", rows, validate):
        return None
    return {
        "category_id": category.currentData(),
        "occurred_on": occurred.date().toString("yyyy-MM-dd"),
        "amount": amount.property("parsed"),
        "payee": payee.text().strip(),
        "note": note.text().strip(),
    }


def edit_budget(parent, category_name, amount=0, note=""):
    value_box = _line(amount, "这一年这一类的预算，人民币")
    note_box = _line(note)
    rows = [("类别", QLabel(category_name)), ("年度预算", value_box), ("备注", note_box)]

    def validate():
        value, error = _parse_number(value_box.text(), "预算", required=True)
        if error:
            return error
        value_box.setProperty("parsed", value)
        return None

    if not run_form(parent, "年度预算", rows, validate):
        return None
    return {"amount": value_box.property("parsed"), "note": note_box.text().strip()}


def edit_category(parent, initial=""):
    name = _line(initial, "例如 父母赡养")
    rows = [("类别名称", name)]

    def validate():
        if not name.text().strip():
            return "请填写类别名称"
        return None

    title = "修改类别名称" if initial else "新的支出类别"
    if not run_form(parent, title, rows, validate):
        return None
    return name.text().strip()


def _confirm_box(parent, text, title, yes, no):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText(yes)
    box.button(QMessageBox.StandardButton.No).setText(no)
    return box.exec() == QMessageBox.StandardButton.Yes


def _ask_stop(parent, row):
    from gxyfi.formatutil import fmt_cny

    box = QMessageBox(parent)
    box.setWindowTitle("停用类别")
    box.setText(
        f"停用「{row['name']}」？这一类有 {row['expense_count']} 笔支出，合计 {fmt_cny(row['expense_total'])}。\n"
        "停用后不能再选它记新账。过去的记录可以留在这一类，也可以现在调整到新类别。"
    )
    only = box.addButton("只停用", QMessageBox.ButtonRole.AcceptRole)
    both = box.addButton("停用并调整分类", QMessageBox.ButtonRole.ActionRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked == only:
        return "stop"
    if clicked == both:
        return "move"
    return None


def _reclassify(parent, source, categories):
    targets = [row for row in categories if row["id"] != source["id"]]
    if not targets:
        QMessageBox.information(parent, "无法调整", "没有其他类别可以接收这些支出。")
        return None
    targets.sort(key=lambda row: (0 if row.get("active", 1) else 1, row.get("sort_order", 0), row["id"]))
    dialog = QDialog(parent)
    dialog.setWindowTitle("调整分类")
    dialog.setMinimumWidth(480)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)
    note = QLabel(
        f"把「{source['name']}」的全部支出、退款、明细上的类别和自动归类规则改到新类别。"
        "如果两边同一年都有预算，金额会加到新类别上。原类别会保留。"
    )
    note.setWordWrap(True)
    layout.addWidget(note)
    form = QFormLayout()
    shown = source["name"] if source.get("active", 1) else source["name"] + "（已停用）"
    form.addRow("原类别", QLabel(shown))
    combo = _combo([
        (row["name"] if row.get("active", 1) else row["name"] + "（已停用）", row["id"])
        for row in targets
    ])
    form.addRow("调整到", combo)
    layout.addLayout(form)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("取消")
    cancel.setObjectName("GhostButton")
    ok = QPushButton("调整")
    ok.setObjectName("PrimaryButton")
    ok.setDefault(True)
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    layout.addLayout(buttons)
    cancel.clicked.connect(dialog.reject)
    ok.clicked.connect(dialog.accept)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return combo.currentData()


def manage_categories(parent):
    """管理全部支出类别。有改动返回 True。"""
    from gxyfi import db
    from gxyfi.formatutil import fmt_cny
    from gxyfi.widgets import FLAT, make_table, selected_id, set_cell

    dialog = QDialog(parent)
    dialog.setWindowTitle("支出类别")
    dialog.resize(720, 520)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)
    note = QLabel("停用后，记新账和归类明细时不会再出现这个类别，已经记过的仍留在原类别。要把过去的支出改到新类别，用「调整分类」。")
    note.setWordWrap(True)
    layout.addWidget(note)
    table = make_table(["类别", "状态", "笔数", "合计", "归类规则"])
    layout.addWidget(table, 1)
    buttons = QHBoxLayout()
    add = QPushButton("新增")
    rename = QPushButton("改名")
    toggle = QPushButton("停用")
    move = QPushButton("调整分类")
    close = QPushButton("关闭")
    for button, name in (
        (add, "PrimaryButton"),
        (rename, "GhostButton"),
        (toggle, "GhostButton"),
        (move, "GhostButton"),
        (close, "GhostButton"),
    ):
        button.setObjectName(name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.addWidget(button)
    buttons.addStretch(1)
    layout.addLayout(buttons)
    state = {"rows": [], "changed": False}

    def fill():
        current = selected_id(table)
        state["rows"] = db.category_overview()
        table.setRowCount(len(state["rows"]))
        for index, row in enumerate(state["rows"]):
            set_cell(table, index, 0, row["name"], row["id"], color=None if row["active"] else FLAT)
            set_cell(table, index, 1, "在用" if row["active"] else "已停用", color=None if row["active"] else FLAT)
            set_cell(table, index, 2, str(row["expense_count"]), align="right")
            set_cell(table, index, 3, fmt_cny(row["expense_total"]), align="right")
            set_cell(table, index, 4, str(row["rule_count"]), align="right")
        if current is not None:
            for index in range(table.rowCount()):
                item = table.item(index, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == current:
                    table.selectRow(index)
                    break
        sync_buttons()

    def current_row():
        category_id = selected_id(table)
        return next((row for row in state["rows"] if row["id"] == category_id), None)

    def sync_buttons():
        row = current_row()
        rename.setEnabled(row is not None)
        move.setEnabled(row is not None)
        toggle.setEnabled(row is not None)
        toggle.setText("恢复" if row and not row["active"] else "停用")

    def add_category():
        name = edit_category(dialog)
        if not name:
            return
        try:
            db.save_category(name)
        except ValueError as exc:
            QMessageBox.warning(dialog, "无法保存", str(exc))
            return
        state["changed"] = True
        fill()

    def rename_category():
        row = current_row()
        if row is None:
            return
        name = edit_category(dialog, row["name"])
        if not name or name == row["name"]:
            return
        try:
            db.save_category(name, row["id"])
        except ValueError as exc:
            QMessageBox.warning(dialog, "无法保存", str(exc))
            return
        state["changed"] = True
        fill()

    def move_category():
        row = current_row()
        if row is None:
            return
        target_id = _reclassify(dialog, row, state["rows"])
        if target_id is None:
            return False
        try:
            db.move_category(row["id"], target_id)
        except ValueError as exc:
            QMessageBox.warning(dialog, "无法调整", str(exc))
            return False
        target = next(item for item in state["rows"] if item["id"] == target_id)
        QMessageBox.information(dialog, "已调整", f"已把「{row['name']}」的历史支出调整到「{target['name']}」。")
        state["changed"] = True
        fill()
        return True

    def toggle_category():
        row = current_row()
        if row is None:
            return
        try:
            if row["active"]:
                action = "stop"
                if row["expense_count"] or row["rule_count"]:
                    action = _ask_stop(dialog, row)
                    if action is None:
                        return
                    if action == "move" and not move_category():
                        return
                elif not _confirm_box(dialog, f"停用「{row['name']}」？之后记新账和归类时不会再出现。", "停用类别", "停用", "取消"):
                    return
                db.set_category_active(row["id"], False)
            else:
                if not _confirm_box(dialog, f"恢复「{row['name']}」？之后可以继续用它记支出。", "恢复类别", "恢复", "取消"):
                    return
                db.set_category_active(row["id"], True)
        except ValueError as exc:
            QMessageBox.warning(dialog, "无法保存", str(exc))
            return
        state["changed"] = True
        fill()

    add.clicked.connect(add_category)
    rename.clicked.connect(rename_category)
    toggle.clicked.connect(toggle_category)
    move.clicked.connect(move_category)
    close.clicked.connect(dialog.accept)
    table.itemSelectionChanged.connect(sync_buttons)
    fill()
    dialog.exec()
    return state["changed"]


def edit_fx(parent, fx):
    usd = _line("" if fx["USD"]["rate"] is None else fx["USD"]["rate"], "1 美元折合人民币")
    hkd = _line("" if fx["HKD"]["rate"] is None else fx["HKD"]["rate"], "1 港币折合人民币")
    token = _line("", "不修改请留空。填写后美股优先走 Finnhub")
    token.setEchoMode(QLineEdit.EchoMode.Password)
    clear_token = QCheckBox("清除已保存的 Token")
    rows = [("美元汇率", usd), ("港币汇率", hkd), ("Finnhub Token", token), ("", clear_token)]
    hint = QLabel("这里保存的是手工汇率。点主界面的“刷新行情”会用 Yahoo Finance 覆盖美元和港币汇率。Token 只留在本机，导出 Excel 时不会带出。")
    hint.setWordWrap(True)
    hint.setObjectName("PageSub")
    rows.append(("", hint))

    def validate():
        usd_value, error = _parse_number(usd.text(), "美元汇率")
        if error:
            return error
        hkd_value, error = _parse_number(hkd.text(), "港币汇率")
        if error:
            return error
        if usd_value is not None and usd_value <= 0:
            return "美元汇率需要大于 0"
        if hkd_value is not None and hkd_value <= 0:
            return "港币汇率需要大于 0"
        usd.setProperty("parsed", usd_value)
        hkd.setProperty("parsed", hkd_value)
        return None

    if not run_form(parent, "汇率与美股备用源", rows, validate):
        return None
    return {
        "USD": usd.property("parsed"),
        "HKD": hkd.property("parsed"),
        "token": token.text().strip(),
        "clear_token": clear_token.isChecked(),
    }


def edit_dca_plan(parent, initial=None):
    initial = initial or {}
    name = _line(initial.get("name", ""), "例如 沪深300ETF联接")
    symbol = _line(initial.get("symbol", ""), "6 位基金代码")
    amount = _line("" if initial.get("amount") is None else initial.get("amount"), "每期扣款金额")
    cycle = _combo([(item, item) for item in CYCLES], initial.get("cycle", "每月"))
    day = QComboBox()
    stopped = initial.get("status") == STOPPED
    if stopped:
        status = QLabel(status_label(initial) + "（用「恢复定投」重新开始）")
        status.setObjectName("PageSub")
    else:
        status = _combo([(item, item) for item in STATUSES if item != STOPPED], initial.get("status", "进行中"))
    started = _line(initial.get("started_on") or "", "YYYY-MM-DD，可留空")
    manual = _line("" if initial.get("manual_nav") is None else initial.get("manual_nav"), "留空则用最近一次确认净值")
    note = _line(initial.get("note", ""))

    def refill_days():
        previous = day.currentData()
        day.blockSignals(True)
        day.clear()
        if cycle.currentData() == "每月":
            for number in range(1, 29):
                day.addItem(f"{number} 日", number)
            target = previous if isinstance(previous, int) and 1 <= previous <= 28 else int(initial.get("debit_day") or 8)
        else:
            for number in range(1, 6):
                day.addItem(WEEKDAYS[number], number)
            fallback = int(initial.get("debit_day") or 1)
            target = previous if isinstance(previous, int) and 1 <= previous <= 5 else min(fallback, 5)
        index = day.findData(target)
        day.setCurrentIndex(0 if index < 0 else index)
        day.blockSignals(False)

    cycle.currentIndexChanged.connect(lambda *_: refill_days())
    refill_days()
    rows = [
        ("基金名称", name),
        ("基金代码", symbol),
        ("每期金额", amount),
        ("扣款周期", cycle),
        ("扣款日", day),
        ("状态", status),
        ("开始日期", started),
        ("最新净值", manual),
        ("备注", note),
    ]

    def validate():
        if not name.text().strip():
            return "请填写基金名称"
        paid, error = _parse_number(amount.text(), "每期金额", required=True, allow_zero=False)
        if error:
            return error
        started_on, error = _parse_optional_date(started.text(), "开始日期")
        if error:
            return error
        nav, error = _parse_number(manual.text(), "最新净值")
        if error:
            return error
        if nav is not None and nav <= 0:
            return "最新净值需要大于 0"
        amount.setProperty("parsed", paid)
        started.setProperty("parsed", started_on)
        manual.setProperty("parsed", nav)
        return None

    if not run_form(parent, "定投计划", rows, validate):
        return None
    return {
        "name": name.text().strip(),
        "symbol": symbol.text().strip(),
        "amount": amount.property("parsed"),
        "cycle": cycle.currentData(),
        "debit_day": day.currentData(),
        "status": STOPPED if stopped else status.currentData(),
        "stopped_on": initial.get("stopped_on") if stopped else None,
        "stop_reason": initial.get("stop_reason", "") if stopped else "",
        "started_on": started.property("parsed"),
        "manual_nav": manual.property("parsed"),
        "note": note.text().strip(),
    }


def stop_dca_plan(parent, plan, last_paid=None):
    """停止定投：选停止日期和原因。返回 {stopped_on, stop_reason} 或 None。"""
    when = _date_edit(date.today().isoformat())
    reason = _combo([(item, item) for item in STOP_REASONS], STOP_REASONS[0])
    detail = _line("", "可留空，例如 限购每日 10 元")
    hint = QLabel(
        "停止后不再按周期扣款，已有份额继续持有、继续按净值计算市值和浮盈。"
        "以后还可以记赎回和红利再投；不能再记停止日期之后的定投扣款。随时可以「恢复定投」。"
        + (f"\n最后一笔定投是 {last_paid}，停止日期不能早于它。" if last_paid else "")
    )
    hint.setWordWrap(True)
    hint.setObjectName("PageSub")
    rows = [
        ("基金", QLabel(f"{plan['name']}  {plan['symbol']}")),
        ("停止日期", when),
        ("原因", reason),
        ("补充说明", detail),
        ("", hint),
    ]

    def validate():
        day = when.date().toString("yyyy-MM-dd")
        if last_paid and day < last_paid:
            return f"停止日期不能早于最后一笔定投（{last_paid}）"
        return None

    if not run_form(parent, "停止定投", rows, validate):
        return None
    text = reason.currentData()
    if detail.text().strip():
        text = f"{text}：{detail.text().strip()}"
    return {"stopped_on": when.date().toString("yyyy-MM-dd"), "stop_reason": text[:80]}


def edit_dca_record(parent, initial=None):
    initial = initial or {}
    kinds = [("期初", "期初"), ("定投", "定投"), ("赎回", "赎回"), ("红利再投", "红利再投")]
    kind = _combo(kinds, initial.get("kind", "定投"))
    trade_date = _date_edit(initial.get("trade_date"))
    amount = _line("" if initial.get("amount") in (None, "") else initial.get("amount"))
    nav = _line("" if initial.get("nav") is None else initial.get("nav"))
    shares = _line("" if initial.get("shares") in (None, "") else initial.get("shares"))
    fee = _line("" if initial.get("fee") in (None, "") else initial.get("fee"), "没有就留空")
    note = _line(initial.get("note", ""))
    hint = QLabel("")
    hint.setWordWrap(True)
    hint.setObjectName("PageSub")

    def sync_kind():
        current = kind.currentData()
        hints = {
            "期初": "期初只记当前份额和总成本。之后的明细不要再把这段份额导入一遍。",
            "定投": "扣款金额按实际扣款填写。确认份额可留空，按（扣款金额 − 手续费）/ 确认净值计算。",
            "赎回": "赎回按份额减少持仓，成本按当时的平均成本等比例扣减。到账金额只留作记录。",
            "红利再投": "红利再投增加份额，不增加成本。",
        }
        amount.setPlaceholderText(
            {"期初": "总成本", "定投": "扣款金额", "赎回": "到账金额，可留空", "红利再投": "红利金额，可留空"}[current]
        )
        shares.setPlaceholderText("定投可留空，其余需要填写" if current == "定投" else "确认份额")
        hint.setText(hints[current])

    kind.currentIndexChanged.connect(lambda *_: sync_kind())
    sync_kind()
    rows = [
        ("类型", kind),
        ("确认日期", trade_date),
        ("金额", amount),
        ("确认净值", nav),
        ("确认份额", shares),
        ("手续费", fee),
        ("备注", note),
        ("", hint),
    ]

    def validate():
        paid, error = _parse_number(amount.text(), "金额")
        if error:
            return error
        price, error = _parse_number(nav.text(), "确认净值")
        if error:
            return error
        quantity, error = _parse_number(shares.text(), "确认份额")
        if error:
            return error
        charge, error = _parse_number(fee.text(), "手续费")
        if error:
            return error
        amount.setProperty("parsed", paid)
        nav.setProperty("parsed", price)
        shares.setProperty("parsed", quantity)
        fee.setProperty("parsed", charge)
        return None

    if not run_form(parent, "定投确认", rows, validate):
        return None
    return {
        "kind": kind.currentData(),
        "trade_date": trade_date.date().toString("yyyy-MM-dd"),
        "amount": amount.property("parsed"),
        "nav": nav.property("parsed"),
        "shares": shares.property("parsed"),
        "fee": fee.property("parsed"),
        "note": note.text().strip(),
    }
