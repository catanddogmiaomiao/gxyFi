"""账户明细的导入、映射确认、修改和撤销导入。"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gxyfi import db, statement
from gxyfi.cloud import _busy
from gxyfi.dialogs import _date_edit, _line, _parse_number, run_form
from gxyfi.formatutil import fmt_money
from gxyfi.widgets import DOWN, GOLD, UP, make_table, set_cell

REQUIRED = {"txn_date"}
PREVIEW_LIMIT = 300
RAW_LIMIT = 200


def _button(text, kind="ghost"):
    button = QPushButton(text)
    button.setObjectName({"primary": "PrimaryButton", "danger": "DangerButton"}.get(kind, "GhostButton"))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _label(text="", name="PageSub"):
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _sample(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return statement._clean(value)


def _totals(rows):
    income = sum(row["amount"] for row in rows if row["amount"] > 0)
    expense = -sum(row["amount"] for row in rows if row["amount"] < 0)
    return income, expense


def _span(rows):
    return f"{rows[0]['txn_date']} ~ {rows[-1]['txn_date']}" if rows else "—"


def fill_txn_table(table, rows, with_line=False):
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        col = 0
        if with_line:
            set_cell(table, index, col, row.get("line", ""), align="right")
            col += 1
        set_cell(table, index, col, row["txn_date"], row.get("id"))
        set_cell(table, index, col + 1, row["txn_time"] or "")
        amount = row["amount"]
        set_cell(table, index, col + 2, fmt_money(amount) if amount > 0 else "", align="right", color=DOWN)
        set_cell(table, index, col + 3, fmt_money(-amount) if amount < 0 else "", align="right", color=UP)
        set_cell(table, index, col + 4, "" if row["balance"] is None else fmt_money(row["balance"]), align="right")
        set_cell(table, index, col + 5, row["counterparty"])
        set_cell(table, index, col + 6, row["summary"])
        set_cell(table, index, col + 7, row["note"])


TXN_HEADERS = ["日期", "时间", "收入", "支出", "交易后余额", "对方户名", "摘要 / 用途", "备注"]


class MappingDialog(QDialog):
    """左边指定每一项在哪一列，右边实时显示按映射读出的结果。"""

    def __init__(self, parent, account, path, rows, mapping, notice, existing, opening):
        super().__init__(parent)
        self.setWindowTitle("明细映射")
        self.setModal(True)
        self.resize(1240, 760)
        self.rows = rows
        self.mapping = copy.deepcopy(mapping)
        self.existing = existing
        self.result = None
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel(f"「{account['name']}」的明细映射")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        layout.addWidget(_label(
            f"文件：{Path(path).name}。左边指定每一项在明细表的哪一列，右边是按这个映射读出来的结果。"
            "核对无误后导入，映射会记在这个账户上，以后导入同样格式的明细不再询问。"
        ))
        if notice:
            layout.addWidget(_label(notice, "Warn"))

        body = QHBoxLayout()
        body.setSpacing(20)
        left = QWidget()
        left.setFixedWidth(400)
        self.form = QFormLayout(left)
        self.form.setContentsMargins(0, 6, 0, 0)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(10)
        self.header_spin = QSpinBox()
        self.header_spin.setRange(1, max(1, min(len(rows), 60)))
        self.header_spin.setValue(self.mapping["header_row"] + 1)
        self.header_spin.setToolTip("行号见右边「原始表格」")
        self.form.addRow("表头在第几行", self.header_spin)
        self.mode = QComboBox()
        for key, label in statement.AMOUNT_MODES:
            self.mode.addItem(label, key)
        self.form.addRow("金额的写法", self.mode)
        self.boxes = {}
        for field, label in statement.FIELDS:
            box = QComboBox()
            box.setMaxVisibleItems(20)
            self.boxes[field] = box
            self.form.addRow(label, box)
            box.currentIndexChanged.connect(self._changed)
        self.invert = QCheckBox("金额正负取反")
        self.invert.setToolTip("收入被读成了负数、支出被读成了正数时勾选")
        self.form.addRow("", self.invert)
        self.opening = QLineEdit()
        self.opening.setPlaceholderText("没有余额列时，从这个数开始累加")
        self.form.addRow("第一笔之前的余额", self.opening)
        self.auto_note = _label("")
        self.form.addRow("", self.auto_note)
        body.addWidget(left)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.summary = _label("", "Figure")
        self.warn = _label("", "Warn")
        right.addWidget(self.summary)
        right.addWidget(self.warn)
        self.tabs = QTabWidget()
        self.preview = make_table(["行号", *TXN_HEADERS])
        self.raw = make_table([])
        self.raw.setSelectionMode(self.raw.SelectionMode.NoSelection)
        self.ignored = make_table(["行号", "原因"])
        self.tabs.addTab(self.preview, "读出的交易")
        self.tabs.addTab(self.raw, "原始表格")
        self.tabs.addTab(self.ignored, "忽略的行")
        right.addWidget(self.tabs, 1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)

        buttons = QHBoxLayout()
        footer = _label("带 * 的必须指定。同一列可以同时作为日期和时间。")
        footer.setWordWrap(False)
        buttons.addWidget(footer)
        buttons.addStretch(1)
        cancel = _button("取消")
        cancel.clicked.connect(self.reject)
        self.ok = _button("确认映射并导入", "primary")
        self.ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        layout.addLayout(buttons)

        self.opening.setText("" if opening is None else f"{opening:.2f}")
        self.header_spin.valueChanged.connect(self._header_changed)
        self.mode.currentIndexChanged.connect(self._changed)
        self.invert.toggled.connect(self._changed)
        self.opening.textChanged.connect(self._changed)
        self._fill_raw()
        self._fill_boxes()
        self._refresh()

    def _fill_raw(self):
        width = max((len(row) for row in self.rows[:RAW_LIMIT]), default=0)
        self.raw.setColumnCount(width + 1)
        self.raw.setHorizontalHeaderLabels(["行"] + [f"{index + 1}" for index in range(width)])
        shown = self.rows[:RAW_LIMIT]
        self.raw.setRowCount(len(shown))
        for index, row in enumerate(shown):
            set_cell(self.raw, index, 0, index + 1, align="right")
            for col, value in enumerate(row):
                set_cell(self.raw, index, col + 1, _sample(value))
        self._mark_header()

    def _mark_header(self):
        header = self.mapping["header_row"]
        for index in range(self.raw.rowCount()):
            for col in range(self.raw.columnCount()):
                item = self.raw.item(index, col)
                if item is not None:
                    item.setForeground(GOLD if index == header else QColor("#E7E1D6"))

    def _fill_boxes(self):
        self._filling = True
        headers = statement.headers_at(self.rows, self.mapping["header_row"])
        for field, box in self.boxes.items():
            box.clear()
            box.addItem("（不导入）", None)
            for name in headers:
                values = statement.column_values(self.rows, self.mapping["header_row"], headers, name, limit=1)
                sample = _sample(values[0]) if values else ""
                text = f"{name}    例：{sample[:18]}" if sample else name
                box.addItem(text, name)
            index = box.findData(self.mapping["columns"].get(field))
            box.setCurrentIndex(max(index, 0))
        self.mode.setCurrentIndex(max(self.mode.findData(self.mapping["amount_mode"]), 0))
        self.invert.setChecked(bool(self.mapping.get("invert")))
        found = sum(1 for name in self.mapping["columns"].values() if name)
        self.auto_note.setText(f"已按列名自动认出 {found} 项，请对照右边核对。")
        self._filling = False
        self._mark_header()

    def _header_changed(self, value):
        guessed = statement.guess_map(self.rows, value - 1)
        guessed["opening"] = self.mapping.get("opening")
        self.mapping = guessed
        self._fill_boxes()
        self._refresh()

    def _changed(self, *_args):
        if self._filling:
            return
        for field, box in self.boxes.items():
            self.mapping["columns"][field] = box.currentData()
        self.mapping["amount_mode"] = self.mode.currentData()
        self.mapping["invert"] = self.invert.isChecked()
        self._refresh()

    def _opening_value(self):
        value, error = _parse_number(self.opening.text(), "第一笔之前的余额", allow_negative=True)
        return value, error

    def _refresh(self):
        mode = self.mapping["amount_mode"]
        needed = REQUIRED | set(statement.MODE_FIELDS[mode])
        for field, label in statement.FIELDS:
            visible = field not in ("amount", "income", "expense", "direction") or field in statement.MODE_FIELDS[mode]
            self.form.setRowVisible(self.boxes[field], visible)
            text = label + (" *" if field in needed else "")
            self.form.labelForField(self.boxes[field]).setText(text)
        has_balance = bool(self.mapping["columns"].get("balance"))
        self.form.setRowVisible(self.opening, not has_balance)

        warnings = []
        opening, error = self._opening_value()
        if error and not has_balance:
            warnings.append(error)
        self.mapping["opening"] = None if has_balance else opening
        lacking = statement.required_missing(self.mapping)
        self.result = None
        if lacking:
            warnings.append("还没有指定：" + "、".join(lacking))
            self.preview.setRowCount(0)
            self.ignored.setRowCount(0)
            self.summary.setText("")
        else:
            result = statement.parse(self.rows, self.mapping)
            self.result = result
            rows = result["rows"]
            income, expense = _totals(rows)
            again = sum(1 for row in rows if row["fingerprint"] in self.existing)
            parts = [f"读出 {len(rows)} 笔", _span(rows), f"收入 {fmt_money(income)}", f"支出 {fmt_money(expense)}"]
            if rows:
                last = statement.daily_balances(rows, self.mapping["opening"])
                parts.append(f"最后余额 {fmt_money(last[max(last)])}")
            self.summary.setText(" · ".join(parts))
            if again:
                warnings.append(f"其中 {again} 笔之前已经导入过，会自动跳过。")
            if not rows:
                warnings.append("按现在的映射一笔也读不出来，请检查表头行和日期列。")
            if result["suggest_invert"]:
                warnings.append(
                    f"有 {result['balance_bad']}/{result['balance_checked']} 处前后余额对不上，看起来收入和支出方向反了，试试勾选「金额正负取反」。"
                )
            elif result["balance_bad"]:
                warnings.append(
                    f"有 {result['balance_bad']} 处「上一笔余额 + 本笔金额」不等于本笔余额，请核对金额列和收支方向。"
                )
            if result["skipped"] and len(result["skipped"]) > len(rows):
                warnings.append("忽略的行比读出来的还多，请检查表头行和日期列。")
            fill_txn_table(self.preview, rows[:PREVIEW_LIMIT], with_line=True)
            self.ignored.setRowCount(len(result["skipped"]))
            for index, (line, reason) in enumerate(result["skipped"]):
                set_cell(self.ignored, index, 0, line, align="right")
                set_cell(self.ignored, index, 1, reason)
            self.tabs.setTabText(2, f"忽略的行（{len(result['skipped'])}）")
        self.warn.setText("\n".join(warnings))
        self.warn.setVisible(bool(warnings))
        count = len(self.result["rows"]) if self.result else 0
        blocked = not count or (error and not has_balance)
        self.ok.setEnabled(not blocked)
        self.ok.setText(f"确认映射并导入 {count} 笔" if count else "确认映射并导入")

    def _accept(self):
        if self.result and self.result["rows"]:
            self.accept()


def confirm_import(parent, account, path, result, existing):
    """映射沿用上次的，直接给出导入摘要。返回 'import'、'adjust' 或 None。"""
    rows = result["rows"]
    again = sum(1 for row in rows if row["fingerprint"] in existing)
    income, expense = _totals(rows)
    dialog = QDialog(parent)
    dialog.setWindowTitle("导入明细")
    dialog.setModal(True)
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(10)
    title = QLabel(f"导入到「{account['name']}」")
    title.setObjectName("DialogTitle")
    layout.addWidget(title)
    layout.addWidget(_label(f"{Path(path).name}。列名和上次一样，沿用这个账户保存的映射。"))
    layout.addWidget(_label(f"读出 {len(rows)} 笔 · {_span(rows)}", "Figure"))
    layout.addWidget(_label(f"收入 {fmt_money(income)} · 支出 {fmt_money(expense)}", "Figure"))
    new = len(rows) - again
    layout.addWidget(_label(
        f"其中新的 {new} 笔会导入，{again} 笔之前导入过会跳过。" if again else f"{new} 笔都是新的。",
        "Good" if new else "Warn",
    ))
    if result["balance_bad"]:
        layout.addWidget(_label(f"有 {result['balance_bad']} 处前后余额对不上，建议点「调整映射」看一下。", "Warn"))
    if result["skipped"]:
        layout.addWidget(_label(f"另有 {len(result['skipped'])} 行不是交易（比如表尾合计），会忽略。"))
    buttons = QHBoxLayout()
    adjust = _button("调整映射")
    buttons.addWidget(adjust)
    buttons.addStretch(1)
    cancel = _button("取消")
    go = _button(f"导入 {new} 笔" if new else "没有新交易", "primary")
    go.setEnabled(new > 0)
    go.setDefault(True)
    buttons.addWidget(cancel)
    buttons.addWidget(go)
    layout.addLayout(buttons)
    picked = {"value": None}

    def done(value):
        picked["value"] = value
        dialog.accept()

    adjust.clicked.connect(lambda: done("adjust"))
    go.clicked.connect(lambda: done("import"))
    cancel.clicked.connect(dialog.reject)
    dialog.exec()
    return picked["value"]


def _opening_default(account_id, rows):
    if not rows:
        return None
    first = rows[0]["txn_date"]
    manual = [row for row in db.list_balances(account_id) if row.get("source") != "明细" and row["as_of_date"] < first]
    return manual[0]["balance"] if manual else 0.0


def import_statement(parent, account):
    """导入一个账户的明细。成功返回给状态栏的一句话，取消返回 None。"""
    path, _filter = QFileDialog.getOpenFileName(
        parent,
        f"选择「{account['name']}」的明细文件",
        str(Path.home() / "Downloads"),
        "明细表 (*.xlsx *.xls *.csv);;所有文件 (*)",
    )
    if not path:
        return None
    try:
        with _busy():
            rows = statement.read_rows(path)
    except statement.StatementError as exc:
        QMessageBox.warning(parent, "读不了这个文件", str(exc))
        return None
    except Exception as exc:
        QMessageBox.warning(parent, "读不了这个文件", f"{exc}\n\n如果是网银导出的 .xls，可以先用 Excel 另存为 .xlsx 再导入。")
        return None

    saved = db.get_import_map(account["id"])
    if saved:
        mapping, added, missing = statement.adapt(saved, rows)
        notice = ""
        if added or missing:
            parts = ["这份明细的列和上次不一样"]
            if added:
                parts.append("多了：" + "、".join(added[:6]))
            if missing:
                parts.append("少了：" + "、".join(missing[:6]))
            notice = "，".join(parts) + "。已尽量沿用原来的映射，请核对后再导入。"
    else:
        mapping = statement.guess_map(rows)
        notice = ""
    existing = {row["fingerprint"] for row in db.list_txns(account["id"])}
    ask = not saved or bool(notice) or bool(statement.required_missing(mapping))
    while True:
        if ask:
            opening = mapping.get("opening")
            if opening is None and not statement.required_missing(mapping):
                opening = _opening_default(account["id"], statement.parse(rows, mapping)["rows"])
            dialog = MappingDialog(parent, account, path, rows, mapping, notice, existing, opening)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            mapping, result = dialog.mapping, dialog.result
            break
        result = statement.parse(rows, mapping)
        choice = confirm_import(parent, account, path, result, existing)
        if choice is None:
            return None
        if choice == "adjust":
            ask = True
            continue
        break

    batch = f"{datetime.now():%Y-%m-%d %H:%M} {Path(path).name}"[:80]
    progress = QProgressDialog("正在导入明细…", None, 0, max(len(result["rows"]), 1), parent)
    progress.setWindowTitle("导入明细")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(400)

    def step(done, total):
        progress.setLabelText(f"已上传到网页端 {done} / {total} 笔…")
        progress.setValue(done)
        QApplication.processEvents()

    try:
        added, skipped = db.import_txns(account["id"], result["rows"], mapping, batch, step)
    finally:
        progress.close()
    from gxyfi.classify_ui import auto_transfers, open_classify

    pairs = auto_transfers() if added else 0
    latest = db.latest_balance(account["id"])
    lines = [f"新增 {added} 笔明细" + (f"，{skipped} 笔之前导入过已跳过" if skipped else "") + "。"]
    if latest:
        lines.append(f"账户余额已按明细更新：{fmt_money(latest['balance'])}（{latest['as_of_date']}）。")
    if pairs:
        lines.append(f"其中 {pairs} 对是自己账户之间的转账，已自动排除，不算支出。")
    lines.append("导入错了可以在「交易明细」里点「撤销一次导入」，或者逐笔修改、删除。")
    waiting = db.pending_count()
    if waiting:
        lines.append(f"\n现在有 {waiting} 笔支出（或退款）等着归类，归类后才计入「支出」和预算。")
        box = QMessageBox(parent)
        box.setWindowTitle("导入完成")
        box.setText("\n".join(lines))
        go = box.addButton("现在归类", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            open_classify(parent)
    else:
        QMessageBox.information(parent, "导入完成", "\n".join(lines))
    return f"「{account['name']}」新增 {added} 笔明细"


def edit_txn(parent, initial=None):
    initial = initial or {}
    when = _date_edit(initial.get("txn_date"))
    clock = _line(initial.get("txn_time", ""), "可不填，如 14:30:00")
    direction = QComboBox()
    direction.addItem("支出", -1)
    direction.addItem("收入", 1)
    amount_value = initial.get("amount")
    if amount_value is not None and amount_value > 0:
        direction.setCurrentIndex(1)
    amount = _line("" if amount_value is None else f"{abs(amount_value):.2f}")
    balance = _line("" if initial.get("balance") is None else f"{initial['balance']:.2f}", "可不填")
    counterparty = _line(initial.get("counterparty", ""))
    summary = _line(initial.get("summary", ""))
    note = _line(initial.get("note", ""))
    rows = [
        ("日期", when), ("时间", clock), ("收支", direction), ("金额", amount),
        ("交易后余额", balance), ("对方户名", counterparty), ("摘要 / 用途", summary), ("备注", note),
    ]
    parsed = {}

    def validate():
        value, error = _parse_number(amount.text(), "金额", required=True)
        if error:
            return error
        after, error = _parse_number(balance.text(), "交易后余额", allow_negative=True)
        if error:
            return error
        if clock.text().strip() and not statement.parse_time(clock.text()):
            return "时间请写成 HH:MM 或 HH:MM:SS"
        parsed.update(amount=abs(value) * direction.currentData(), balance=after)
        return None

    if not run_form(parent, "交易明细", rows, validate):
        return None
    return {
        "txn_date": when.date().toString("yyyy-MM-dd"),
        "txn_time": statement.parse_time(clock.text()) if clock.text().strip() else "",
        "amount": parsed["amount"],
        "balance": parsed["balance"],
        "counterparty": counterparty.text().strip(),
        "summary": summary.text().strip(),
        "note": note.text().strip(),
    }


def undo_import(parent, account):
    """列出这个账户的每次导入，撤销选中的那一次。返回是否有改动。"""
    batches = db.txn_batches(account["id"])
    if not batches:
        QMessageBox.information(parent, "撤销导入", "这个账户还没有导入过明细。")
        return False
    dialog = QDialog(parent)
    dialog.setWindowTitle("撤销一次导入")
    dialog.setModal(True)
    dialog.resize(620, 420)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(10)
    title = QLabel(f"「{account['name']}」的导入记录")
    title.setObjectName("DialogTitle")
    layout.addWidget(title)
    layout.addWidget(_label("撤销会删除那一次导入的全部明细，余额随之重算。映射错了时，先撤销，再重新导入并调整映射。"))
    listing = QListWidget()
    for row in batches:
        item = QListWidgetItem(f"{row['batch'] or '（未命名）'}\n    {row['n']} 笔 · {row['first']} ~ {row['last']}")
        item.setData(Qt.ItemDataRole.UserRole, row["batch"])
        listing.addItem(item)
    listing.setCurrentRow(0)
    layout.addWidget(listing, 1)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = _button("关闭")
    remove = _button("撤销选中的这次导入", "danger")
    buttons.addWidget(cancel)
    buttons.addWidget(remove)
    layout.addLayout(buttons)
    changed = {"value": False}

    def go():
        item = listing.currentItem()
        if item is None:
            return
        batch = item.data(Qt.ItemDataRole.UserRole)
        box = QMessageBox(dialog)
        box.setWindowTitle("请确认")
        box.setText(f"删除这次导入的全部明细？\n{item.text()}")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("撤销导入")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        db.delete_txn_batch(account["id"], batch)
        changed["value"] = True
        listing.takeItem(listing.row(item))
        if listing.count() == 0:
            dialog.accept()

    remove.clicked.connect(go)
    cancel.clicked.connect(dialog.accept)
    dialog.exec()
    return changed["value"]
