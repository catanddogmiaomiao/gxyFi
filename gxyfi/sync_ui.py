"""「网页同步」窗口：登录网页账号、首次登录时挑选要上传的本机记录、退出登录。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gxyfi import cloud, db

RECORD_KEYS = ("accounts", "balances", "holdings", "properties", "expenses", "budgets", "dcaPlans", "dcaRecords", "txns")


def _button(text, kind="ghost"):
    button = QPushButton(text)
    button.setObjectName({"primary": "PrimaryButton", "danger": "DangerButton"}.get(kind, "GhostButton"))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _note(text):
    label = QLabel(text)
    label.setObjectName("PageSub")
    label.setWordWrap(True)
    return label


def status_text():
    cfg = cloud.config()
    if not cloud.enabled():
        return "账本只在这台电脑。登录网页账号后，电脑、手机和网页用同一本账。"
    if cloud.expired():
        return f"网页账号 {cfg['user']} 登录已过期，现在看到的是上次同步的内容，不能记账。"
    when = cfg["synced_at"] or "—"
    return f"已连接网页账号 {cfg['user']}，在电脑上记的账会直接存到网页端。上次同步 {when}。"


def choose_upload(parent, items, record_count):
    """返回要上传的记录；返回 None 表示取消登录。"""
    dialog = QDialog(parent)
    dialog.setWindowTitle("这台电脑上已有记录")
    dialog.setModal(True)
    dialog.resize(640, 560)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)
    if items:
        layout.addWidget(_note(
            f"这台电脑上有 {record_count} 条记录。下面是网页账号里还没有的，勾选的会追加到网页账号，"
            "其余改用网页上的账。名称相同、或最近余额相同的账户视为同一个账户，不会重复上传。"
            "无论怎么选，都会先把这台电脑上的账备份成一个文件。"
        ))
    else:
        layout.addWidget(_note(
            f"这台电脑上的 {record_count} 条记录网页账号里都已经有了，登录后改用网页上的账。"
            "这台电脑上的账会先备份成一个文件。"
        ))
    listing = QListWidget()
    section = None
    for index, item in enumerate(items):
        if item["section"] != section:
            section = item["section"]
            header = QListWidgetItem(section)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setForeground(Qt.GlobalColor.darkYellow)
            listing.addItem(header)
        entry = QListWidgetItem("    " + item["label"])
        entry.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        entry.setCheckState(Qt.CheckState.Checked if item["checked"] else Qt.CheckState.Unchecked)
        entry.setData(Qt.ItemDataRole.UserRole, index)
        listing.addItem(entry)
    listing.setVisible(bool(items))
    layout.addWidget(listing, 1)
    if any(item["kind"] == "balance" and not item["checked"] for item in items):
        layout.addWidget(_note("没勾的余额快照属于网页端已有的账户，日期和网页上的不同，通常是重复录入，按需勾选。"))
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = _button("取消")
    skip = _button("不上传，改用网页上的账", "danger" if items else "ghost")
    send = _button("上传勾选的记录", "primary")
    buttons.addWidget(cancel)
    if items:
        buttons.addWidget(skip)
        buttons.addWidget(send)
    else:
        skip.setText("改用网页上的账")
        skip.setObjectName("PrimaryButton")
        buttons.addWidget(skip)
    layout.addLayout(buttons)
    picked = {"value": None}

    def take(upload):
        if not upload:
            picked["value"] = []
        else:
            chosen = []
            for row in range(listing.count()):
                entry = listing.item(row)
                index = entry.data(Qt.ItemDataRole.UserRole)
                if index is not None and entry.checkState() == Qt.CheckState.Checked:
                    chosen.append(items[index])
            picked["value"] = chosen
        dialog.accept()

    cancel.clicked.connect(dialog.reject)
    skip.clicked.connect(lambda: take(False))
    send.clicked.connect(lambda: take(True))
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return picked["value"]


def connect(parent, url, username, password):
    """登录并切到网页账号。成功返回给用户看的结果，取消返回 None。"""
    url = cloud.server_url(url)
    username = username.strip()
    if not username or not password:
        raise cloud.CloudError("请填写用户名和密码")
    with cloud._busy():
        token = cloud.login(url, username, password)
        remote = cloud.fetch_state({"url": url, "token": token})
    was_mirror = cloud.enabled()
    local = db.local_snapshot()
    count = sum(len(local[key]) for key in RECORD_KEYS)
    selected, matches, backup = [], None, None
    if not was_mirror and count:
        items, matches = cloud.local_only(local, remote)
        selected = choose_upload(parent, items, count)
        if selected is None:
            return None
        backup = cloud.backup_local()
    cloud.save_login(url, username, token)
    cloud.apply(remote)
    sent = 0
    if selected:
        sent = cloud.upload(selected, local, matches)
    lines = [f"已连接网页账号 {username}。电脑、手机和网页现在是同一本账。"]
    if sent:
        lines.append(f"已上传 {sent} 条这台电脑上的记录。")
    if backup:
        lines.append(f"原来这台电脑上的账备份在：{backup}")
    return "\n".join(lines)


class SyncDialog(QDialog):
    def __init__(self, parent, pull):
        super().__init__(parent)
        self.setWindowTitle("网页同步")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._pull = pull
        self.changed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        self.status = _note(status_text())
        layout.addWidget(self.status)
        cfg = cloud.config()
        if cloud.enabled() and not cloud.expired():
            form = QFormLayout()
            form.addRow("网页端", QLabel(cfg["url"]))
            form.addRow("账号", QLabel(cfg["user"]))
            form.addRow("上次同步", QLabel(cfg["synced_at"] or "—"))
            layout.addLayout(form)
            layout.addWidget(_note("登录后，记账、修改、删除都会直接存到网页端，再下载回这台电脑显示。没有网络时只能查看，不能记账。刷新行情也由网页端去取。"))
            buttons = QHBoxLayout()
            logout = _button("退出登录", "danger")
            logout.clicked.connect(self.logout)
            now = _button("立即同步", "primary")
            now.clicked.connect(self.sync_now)
            close = _button("关闭")
            close.clicked.connect(self.accept)
            buttons.addWidget(logout)
            buttons.addStretch(1)
            buttons.addWidget(close)
            buttons.addWidget(now)
            layout.addLayout(buttons)
            return
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        self.url = QLineEdit(cfg["url"])
        self.user = QLineEdit(cfg["user"])
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("网页端地址", self.url)
        form.addRow("用户名", self.user)
        form.addRow("密码", self.password)
        layout.addLayout(form)
        layout.addWidget(_note("使用网页版和手机上的同一个账号。网页端不开放注册，账号由管理员在 Cloudflare 里配置。"))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = _button("取消")
        cancel.clicked.connect(self.reject)
        ok = _button("登录并同步", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.login)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        (self.password if cfg["user"] else self.user).setFocus()

    def login(self):
        try:
            message = connect(self, self.url.text(), self.user.text(), self.password.text())
        except cloud.CloudError as exc:
            QMessageBox.warning(self, "没有登录", str(exc))
            return
        if message is None:
            return
        self.changed = True
        QMessageBox.information(self, "网页同步", message)
        self.accept()

    def sync_now(self):
        self._pull()
        self.accept()

    def logout(self):
        box = QMessageBox(self)
        box.setWindowTitle("退出网页账号？")
        box.setText("退出后会清空这台电脑上的缓存。账仍保存在网页端，下次登录会重新下载。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("退出并清空缓存")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        with cloud._busy():
            cloud.disconnect()
        self.changed = True
        self.accept()
