"""「归类明细」窗口：把导入的明细确认成支出、退款或不算支出。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gxyfi import classify, db
from gxyfi.formatutil import fmt_money
from gxyfi.widgets import DOWN, GOLD, MUTED, UP, make_table, set_cell

VIEW_PENDING = "pending"
VIEW_DONE = "done"


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


def auto_transfers():
    """自己账户之间对得上的转账直接排除，返回配对数。"""
    pairs = classify.find_transfers(db.list_txns_all_open())
    items = [{"id": txn_id, "treat": "转账"} for pair in pairs for txn_id in pair]
    if items:
        db.classify_txns(items)
    return len(pairs)


class ClassifyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("归类明细")
        self.setModal(True)
        self.resize(1240, 780)
        self.changed = False
        self.rows = []
        self.suggestions = {}
        self.transfers = auto_transfers()
        if self.transfers:
            self.changed = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel("把明细归到支出")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        layout.addWidget(_label(
            "这里列出导入的明细里还没归类的支出，以及看起来像退款的收入。程序先给出建议，你确认后才计入「支出」和预算。"
            "可以按住 Ctrl 或 Shift 多选，一次归一类。"
        ))
        self.summary = _label("", "Figure")
        layout.addWidget(self.summary)

        bar = QHBoxLayout()
        self.view = QComboBox()
        self.view.addItem("待归类", VIEW_PENDING)
        self.view.addItem("已归类（可以改）", VIEW_DONE)
        self.view.currentIndexChanged.connect(self.reload)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索对方户名、摘要")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._fill)
        bar.addWidget(self.view)
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.rules_btn = _button("已记住的规则")
        self.rules_btn.clicked.connect(self.edit_rules)
        bar.addWidget(self.rules_btn)
        self.confirm_all = _button("确认全部建议", "primary")
        self.confirm_all.clicked.connect(self.accept_all)
        bar.addWidget(self.confirm_all)
        layout.addLayout(bar)

        self.table = make_table(["日期", "账户", "对方户名", "摘要", "支出", "收入", "建议 / 当前归类", "依据"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.cellDoubleClicked.connect(lambda *_: self.accept_selected())
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(QLabel("选中的："))
        self.confirm_btn = _button("确认建议")
        self.confirm_btn.clicked.connect(self.accept_selected)
        actions.addWidget(self.confirm_btn)
        actions.addSpacing(12)
        actions.addWidget(QLabel("归为"))
        self.category = QComboBox()
        self.category.setMinimumWidth(120)
        actions.addWidget(self.category)
        put = _button("归入这一类", "primary")
        put.clicked.connect(self.put_category)
        actions.addWidget(put)
        skip = _button("不算支出")
        skip.setToolTip("转账给别人以外的资金往来，比如还信用卡、买理财、存定期")
        skip.clicked.connect(self.mark_skip)
        actions.addWidget(skip)
        self.reset_btn = _button("恢复待归类", "danger")
        self.reset_btn.clicked.connect(self.mark_pending)
        actions.addWidget(self.reset_btn)
        actions.addSpacing(12)
        self.learn = QCheckBox("记住：同一对方以后自动给出同样的建议")
        self.learn.setChecked(True)
        actions.addWidget(self.learn)
        actions.addStretch(1)
        close = _button("完成")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.reload()

    def reload(self):
        categories = db.list_categories()
        self.names = {row["id"]: row["name"] for row in categories}
        current = self.category.currentData()
        self.category.clear()
        for row in categories:
            self.category.addItem(row["name"], row["id"])
        if current is not None:
            self.category.setCurrentIndex(max(self.category.findData(current), 0))
        rules = db.list_rules()
        everything = db._all(
            """
            SELECT t.*, a.name AS account_name FROM account_txns t JOIN accounts a ON a.id = t.account_id
            ORDER BY t.txn_date DESC, t.id DESC
            """
        )
        pending_rows = classify.pending([row for row in everything if not row["treat"]], rules)
        history = [row for row in everything if row["treat"] in ("支出", "退款")]
        self.suggestions = classify.suggest(pending_rows, rules, db.list_expenses(), categories, history)
        done_view = self.view.currentData() == VIEW_DONE
        if done_view:
            self.rows = [row for row in everything if row["treat"]]
        else:
            self.rows = pending_rows
        suggested = sum(1 for row in pending_rows if row["id"] in self.suggestions and not self.suggestions[row["id"]]["merge"])
        merges = sum(1 for row in pending_rows if self.suggestions.get(row["id"], {}).get("merge"))
        parts = [f"待归类 {len(pending_rows)} 笔", f"其中 {suggested} 笔已有建议"]
        if merges:
            parts.append(f"{merges} 笔可能和手工记录重复（需逐笔确认）")
        if self.transfers:
            parts.append(f"刚刚自动排除了 {self.transfers} 对自己账户间的转账")
        self.summary.setText(" · ".join(parts))
        self.confirm_all.setVisible(not done_view)
        self.confirm_all.setEnabled(suggested > 0)
        self.confirm_all.setText(f"确认全部建议（{suggested}）" if suggested else "确认全部建议")
        self.confirm_btn.setVisible(not done_view)
        self.reset_btn.setVisible(done_view)
        self.rules_btn.setText(f"已记住的规则（{len(rules)}）")
        self._fill()

    def edit_rules(self):
        RulesDialog(self.names, self).exec()
        self.reload()

    def _fill(self):
        word = self.search.text().strip().lower()
        rows = [row for row in self.rows if not word or word in f"{row['counterparty']} {row['summary']}".lower()]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            amount = row["amount"]
            set_cell(self.table, index, 0, row["txn_date"], row["id"])
            set_cell(self.table, index, 1, row["account_name"])
            set_cell(self.table, index, 2, row["counterparty"])
            set_cell(self.table, index, 3, row["summary"])
            set_cell(self.table, index, 4, fmt_money(-amount) if amount < 0 else "", align="right", color=UP)
            set_cell(self.table, index, 5, fmt_money(amount) if amount > 0 else "", align="right", color=DOWN)
            if row["treat"]:
                set_cell(self.table, index, 6, classify.describe(row["treat"], row["category_id"], self.names))
                set_cell(self.table, index, 7, "")
                continue
            hint = self.suggestions.get(row["id"])
            if hint is None:
                set_cell(self.table, index, 6, "请选择" if amount < 0 else "退款？请选择类别", color=MUTED)
                set_cell(self.table, index, 7, "")
            else:
                text = "合并到手工记录" if hint["merge"] else classify.describe(hint["treat"], hint["category_id"], self.names)
                set_cell(self.table, index, 6, text, color=UP if hint["merge"] else GOLD)
                set_cell(self.table, index, 7, hint["reason"], color=MUTED)

    def _selected(self):
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        lookup = {row["id"]: row for row in self.rows}
        rows = [lookup[txn_id] for txn_id in ids if txn_id in lookup]
        if not rows:
            QMessageBox.information(self, "未选择", "请先在表格里选中要处理的明细，可以按住 Ctrl 或 Shift 多选。")
        return rows

    def _apply(self, pairs):
        """pairs: [(明细, 归类)]，归类含 treat、category_id、expense_id。"""
        if not pairs:
            return
        items, rules = [], []
        for txn, choice in pairs:
            items.append({"id": txn["id"], "treat": choice["treat"], "category_id": choice.get("category_id"), "expense_id": choice.get("expense_id")})
            if self.learn.isChecked() and not choice.get("expense_id"):
                rule = classify.rule_for(txn, choice["treat"], choice.get("category_id"))
                if rule and all(existing["keyword"] != rule["keyword"] or existing["field"] != rule["field"] for existing in rules):
                    rules.append(rule)
        db.classify_txns(items, rules)
        self.changed = True
        self.reload()

    def accept_all(self):
        pairs = [
            (row, self.suggestions[row["id"]]) for row in self.rows
            if row["id"] in self.suggestions and not self.suggestions[row["id"]]["merge"]
        ]
        if not pairs:
            return
        box = QMessageBox(self)
        box.setWindowTitle("确认全部建议")
        box.setText(f"按建议归类 {len(pairs)} 笔？可能重复的手工记录不在其中，需要逐笔确认。\n归错了可以在「已归类」里改。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("确认")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        if box.exec() == QMessageBox.StandardButton.Yes:
            self._apply(pairs)

    def accept_selected(self):
        if self.view.currentData() == VIEW_DONE:
            return
        rows = self._selected()
        pairs = [(row, self.suggestions[row["id"]]) for row in rows if row["id"] in self.suggestions]
        missing = len(rows) - len(pairs)
        self._apply(pairs)
        if missing:
            QMessageBox.information(self, "还有没建议的", f"有 {missing} 笔没有建议，请在下面选一个类别后点「归入这一类」，或点「不算支出」。")

    def put_category(self):
        rows = self._selected()
        category_id = self.category.currentData()
        if not rows or category_id is None:
            return
        self._apply([(row, {"treat": "支出" if row["amount"] < 0 else "退款", "category_id": category_id}) for row in rows])

    def mark_skip(self):
        rows = self._selected()
        self._apply([(row, {"treat": "不计"}) for row in rows])

    def mark_pending(self):
        rows = self._selected()
        if rows:
            db.classify_txns([{"id": row["id"], "treat": ""} for row in rows])
            self.changed = True
            self.reload()


class RulesDialog(QDialog):
    FIELDS = {"counterparty": "对方户名", "summary": "摘要"}

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("已记住的规则")
        self.setModal(True)
        self.resize(640, 480)
        self.names = names
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel("已记住的规则")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        layout.addWidget(_label(
            "归类时勾选「记住」会留下这些规则，以后同样的明细会自动给出建议（仍需你确认）。"
            "规则错了就删掉；删除不影响已经归好类的明细。"
        ))
        self.table = make_table(["按", "包含", "建议归为"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        remove = _button("删除所选", "danger")
        remove.clicked.connect(self.remove)
        actions.addWidget(remove)
        actions.addStretch(1)
        close = _button("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.reload()

    def reload(self):
        rules = db.list_rules()
        self.table.setRowCount(len(rules))
        for index, rule in enumerate(rules):
            set_cell(self.table, index, 0, self.FIELDS.get(rule["field"], rule["field"]), rule["id"])
            set_cell(self.table, index, 1, rule["keyword"])
            set_cell(self.table, index, 2, classify.describe(rule["treat"], rule["category_id"], self.names))

    def remove(self):
        ids = [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in self.table.selectionModel().selectedRows()]
        if not ids:
            QMessageBox.information(self, "未选择", "请先选中要删除的规则。")
            return
        db.delete_rules(ids)
        self.reload()


def open_classify(parent):
    dialog = ClassifyDialog(parent)
    dialog.exec()
    return dialog.changed
