from __future__ import annotations

from datetime import datetime

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

from gxyfi.dca import CYCLES, STATUSES, WEEKDAYS
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


def edit_category(parent):
    name = _line("", "例如 父母赡养")
    rows = [("类别名称", name)]

    def validate():
        if not name.text().strip():
            return "请填写类别名称"
        return None

    if not run_form(parent, "新的支出类别", rows, validate):
        return None
    return name.text().strip()


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
    status = _combo([(item, item) for item in STATUSES], initial.get("status", "进行中"))
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
        "status": status.currentData(),
        "started_on": started.property("parsed"),
        "manual_nav": manual.property("parsed"),
        "note": note.text().strip(),
    }


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
