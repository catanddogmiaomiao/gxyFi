"""本机 SQLite 台账。桌面版是单用户，数据不上传。"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from gxyfi import dca, statement

ACCOUNT_KINDS = ["现金", "活期", "定期", "货币基金", "公积金", "其他"]
PROPERTY_KINDS = ["住宅", "商铺", "车位", "土地", "其他"]
CURRENCIES = ["CNY", "USD", "HKD"]
CATEGORIES = ["居住", "餐饮", "交通", "教育", "医疗", "保险", "日用", "人情", "娱乐", "其他"]
STALE_DAYS = 35

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    institution TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '活期',
    currency TEXT NOT NULL DEFAULT 'CNY',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS balance_entries (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    as_of_date TEXT NOT NULL,
    balance REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (account_id, as_of_date)
);
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    quantity REAL NOT NULL,
    currency TEXT NOT NULL,
    avg_cost REAL,
    manual_price REAL,
    account_label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    price REAL,
    prev_close REAL,
    currency TEXT NOT NULL DEFAULT '',
    quoted_at TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (market, symbol)
);
CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '住宅',
    city TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    area_sqm REAL,
    acquired_on TEXT,
    purchase_price REAL,
    current_value REAL,
    valued_on TEXT,
    mortgage_balance REAL NOT NULL DEFAULT 0,
    monthly_rent REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expense_categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES expense_categories(id),
    occurred_on TEXT NOT NULL,
    amount REAL NOT NULL,
    payee TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_lines (
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    category_id INTEGER NOT NULL REFERENCES expense_categories(id),
    amount REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (year, category_id)
);
CREATE TABLE IF NOT EXISTS dca_plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    amount REAL NOT NULL,
    cycle TEXT NOT NULL DEFAULT '每月',
    debit_day INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT '进行中',
    started_on TEXT,
    manual_nav REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dca_records (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES dca_plans(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    nav REAL,
    shares REAL NOT NULL DEFAULT 0,
    fee REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_txns (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    txn_date TEXT NOT NULL,
    txn_time TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL,
    balance REAL,
    counterparty TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    batch TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS account_txns_by_account ON account_txns(account_id, txn_date);
CREATE TABLE IF NOT EXISTS txn_rules (
    id INTEGER PRIMARY KEY,
    field TEXT NOT NULL,
    keyword TEXT NOT NULL,
    treat TEXT NOT NULL,
    category_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (field, keyword)
);
"""

ADDED_COLUMNS = (
    ("accounts", "import_map", "TEXT NOT NULL DEFAULT ''"),
    ("balance_entries", "source", "TEXT NOT NULL DEFAULT '手工'"),
    ("account_txns", "treat", "TEXT NOT NULL DEFAULT ''"),
    ("account_txns", "category_id", "INTEGER"),
    ("expenses", "txn_id", "INTEGER"),
    ("expenses", "source", "TEXT NOT NULL DEFAULT '手工'"),
)
TXN_COLUMNS = (
    "id", "account_id", "txn_date", "txn_time", "amount", "balance", "counterparty",
    "summary", "note", "fingerprint", "batch", "treat", "category_id", "created_at", "updated_at",
)
TREATS = ("", "支出", "退款", "不计", "转账")


def db_path() -> Path:
    override = os.environ.get("GXYFI_DB")
    if override:
        path = Path(override)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "gxyFi"
    base.mkdir(parents=True, exist_ok=True)
    return base / "gxyfi.db"


def now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def r2(value):
    return None if value is None else round(float(value), 2)


def r4(value):
    return None if value is None else round(float(value), 4)


@contextmanager
def connect():
    conn = sqlite3.connect(db_path())
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _all(sql, args=()):
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]


def _one(sql, args=()):
    with connect() as conn:
        row = conn.execute(sql, args).fetchone()
        return None if row is None else dict(row)


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, spec in ADDED_COLUMNS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
        count = conn.execute("SELECT COUNT(*) AS n FROM expense_categories").fetchone()["n"]
        if count == 0:
            conn.executemany(
                "INSERT INTO expense_categories(name, sort_order) VALUES(?, ?)",
                [(name, index) for index, name in enumerate(CATEGORIES)],
            )
        if conn.execute("SELECT 1 FROM settings WHERE key='fx'").fetchone() is None:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('fx', ?)",
                (json.dumps({"USD": _fx_blank(), "HKD": _fx_blank()}, ensure_ascii=False),),
            )


def _fx_blank():
    return {"rate": None, "source": "未设置", "at": ""}


def _remote():
    from gxyfi import cloud

    return cloud if cloud.enabled() else None


CLOUD_TABLES = (
    ("accounts", "accounts"),
    ("balance_entries", "balances"),
    ("holdings", "holdings"),
    ("quotes", "quotes"),
    ("properties", "properties"),
    ("expense_categories", "categories"),
    ("expenses", "expenses"),
    ("budget_lines", "budgets"),
    ("dca_plans", "dcaPlans"),
    ("dca_records", "dcaRecords"),
    ("txn_rules", "txnRules"),
)


def replace_from_cloud(state):
    """用网站返回的整本账覆盖本机副本，保留本机设置。"""
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table, _key in reversed(CLOUD_TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table, key in CLOUD_TABLES:
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
            rows = state.get(key) or []
            if not rows:
                continue
            usable = [name for name in columns if name in rows[0] or name == "created_at"]
            stamp = now_text()
            values = []
            for row in rows:
                values.append(tuple(row.get(name) or stamp if name == "created_at" else row.get(name) for name in usable))
            marks = ", ".join("?" for _ in usable)
            conn.executemany(f"INSERT INTO {table}({', '.join(usable)}) VALUES({marks})", values)
        fx = {"USD": _fx_blank(), "HKD": _fx_blank()}
        for row in state.get("fx") or []:
            if row.get("code") in fx and row.get("rate") is not None:
                fx[row["code"]] = {"rate": float(row["rate"]), "source": row.get("source") or "手工", "at": row.get("quoted_at") or ""}
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES('fx', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (json.dumps(fx, ensure_ascii=False),),
        )
        if "txns" in state:
            conn.execute("DELETE FROM account_txns")
            marks = ", ".join("?" for _ in TXN_COLUMNS)
            conn.executemany(
                f"INSERT INTO account_txns({', '.join(TXN_COLUMNS)}) VALUES({marks})",
                [tuple(row.get(name) for name in TXN_COLUMNS) for row in state["txns"]],
            )
        else:
            conn.execute("DELETE FROM account_txns WHERE account_id NOT IN (SELECT id FROM accounts)")
        if state.get("txnRev") is not None and "txns" in state:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('txn_rev', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (state["txnRev"],),
            )


def clear_records():
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM account_txns")
        for table, _key in reversed(CLOUD_TABLES):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM settings WHERE key IN ('fx', 'txn_rev')")
    init_db()


def local_snapshot():
    snapshot = {key: _all(f"SELECT * FROM {table}") for table, key in CLOUD_TABLES}
    snapshot["txns"] = _all("SELECT * FROM account_txns ORDER BY account_id, txn_date, txn_time, id")
    snapshot["fx"] = [
        {"code": code, "rate": item["rate"], "source": item["source"], "quoted_at": item["at"]}
        for code, item in fx_rates().items()
        if item["rate"] is not None
    ]
    return snapshot


def get_setting(key, default=None):
    row = _one("SELECT value FROM settings WHERE key=?", (key,))
    return default if row is None else row["value"]


def set_setting(key, value):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def fx_rates():
    raw = get_setting("fx")
    data = json.loads(raw) if raw else {}
    out = {}
    for code in ("USD", "HKD"):
        item = data.get(code) or {}
        rate = item.get("rate")
        out[code] = {
            "rate": None if rate is None else float(rate),
            "source": item.get("source") or "未设置",
            "at": item.get("at") or "",
        }
    return out


def save_fx_rates(rates):
    if remote := _remote():
        return remote.save_fx_rates(rates)
    current = fx_rates()
    for code, item in rates.items():
        current[code] = {
            "rate": None if item.get("rate") is None else float(item["rate"]),
            "source": item.get("source") or "手工",
            "at": item.get("at") or now_text(),
        }
    set_setting("fx", json.dumps(current, ensure_ascii=False))


def to_cny(amount, currency, rates=None):
    if amount is None:
        return None
    if currency == "CNY":
        return float(amount)
    rates = fx_rates() if rates is None else rates
    item = rates.get(currency) or {}
    rate = item.get("rate")
    if rate is None:
        return None
    return float(amount) * float(rate)


def list_accounts():
    return _all("SELECT * FROM accounts ORDER BY id")


def get_account(account_id):
    return _one("SELECT * FROM accounts WHERE id=?", (account_id,))


def save_account(data, account_id=None):
    if remote := _remote():
        return remote.save_account(data, account_id)
    name = data["name"].strip()
    if not name:
        raise ValueError("请填写账户名称")
    fields = (
        name,
        data.get("institution", "").strip(),
        data.get("kind") or "活期",
        data.get("currency") or "CNY",
        data.get("note", "").strip(),
    )
    with connect() as conn:
        if account_id:
            conn.execute(
                """
                UPDATE accounts
                SET name=?, institution=?, kind=?, currency=?, note=?
                WHERE id=?
                """,
                (*fields, account_id),
            )
            return account_id
        cur = conn.execute(
            """
            INSERT INTO accounts(name, institution, kind, currency, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (*fields, now_text()),
        )
        return cur.lastrowid


def delete_account(account_id):
    if remote := _remote():
        return remote.delete_account(account_id)
    with connect() as conn:
        _unlink(conn, "account_id=?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))


def list_balances(account_id):
    return _all(
        """
        SELECT * FROM balance_entries
        WHERE account_id=?
        ORDER BY as_of_date DESC, id DESC
        """,
        (account_id,),
    )


def latest_balance(account_id):
    return _one(
        """
        SELECT * FROM balance_entries
        WHERE account_id=?
        ORDER BY as_of_date DESC, id DESC
        LIMIT 1
        """,
        (account_id,),
    )


def save_balance(data, entry_id=None):
    if remote := _remote():
        return remote.save_balance(data, entry_id)
    account_id = int(data["account_id"])
    as_of = data["as_of_date"]
    balance = r2(data["balance"])
    note = data.get("note", "").strip()
    with connect() as conn:
        if entry_id:
            previous = conn.execute("SELECT account_id FROM balance_entries WHERE id=?", (entry_id,)).fetchone()
            try:
                conn.execute(
                    """
                    UPDATE balance_entries
                    SET account_id=?, as_of_date=?, balance=?, note=?, source='手工'
                    WHERE id=?
                    """,
                    (account_id, as_of, balance, note, entry_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("这个账户在这一天已经有一条余额") from exc
            _rebuild_balances(conn, account_id)
            if previous is not None and previous["account_id"] != account_id:
                _rebuild_balances(conn, previous["account_id"])
            return entry_id
        conn.execute(
            """
            INSERT INTO balance_entries(account_id, as_of_date, balance, note, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(account_id, as_of_date) DO UPDATE SET
                balance=excluded.balance,
                note=excluded.note,
                source='手工'
            """,
            (account_id, as_of, balance, note, now_text()),
        )
        row = conn.execute(
            "SELECT id FROM balance_entries WHERE account_id=? AND as_of_date=?",
            (account_id, as_of),
        ).fetchone()
        entry_id = row["id"]
        _rebuild_balances(conn, account_id)
        return entry_id


def delete_balance(entry_id):
    if remote := _remote():
        return remote.delete_balance(entry_id)
    with connect() as conn:
        row = conn.execute("SELECT account_id FROM balance_entries WHERE id=?", (entry_id,)).fetchone()
        conn.execute("DELETE FROM balance_entries WHERE id=?", (entry_id,))
        if row is not None:
            _rebuild_balances(conn, row["account_id"])


def get_import_map(account_id):
    row = _one("SELECT import_map FROM accounts WHERE id=?", (account_id,))
    if not row or not row["import_map"]:
        return None
    try:
        return json.loads(row["import_map"])
    except ValueError:
        return None


def _rebuild_balances(conn, account_id):
    row = conn.execute("SELECT import_map FROM accounts WHERE id=?", (account_id,)).fetchone()
    try:
        opening = json.loads(row["import_map"]).get("opening") if row and row["import_map"] else None
    except ValueError:
        opening = None
    anchors = [
        (row["as_of_date"], row["balance"])
        for row in conn.execute(
            """
            SELECT as_of_date, balance FROM balance_entries
            WHERE account_id=? AND source!='明细' ORDER BY as_of_date, id
            """,
            (account_id,),
        )
    ]
    txns = [
        dict(item)
        for item in conn.execute(
            "SELECT txn_date, amount, balance FROM account_txns WHERE account_id=? ORDER BY txn_date, txn_time, id",
            (account_id,),
        )
    ]
    conn.execute("DELETE FROM balance_entries WHERE account_id=? AND source='明细'", (account_id,))
    stamp = now_text()
    manual_dates = {day for day, _balance in anchors}
    for as_of, balance in statement.snapshot_points(txns, opening, anchors):
        if as_of in manual_dates:
            continue
        conn.execute(
            """
            INSERT INTO balance_entries(account_id, as_of_date, balance, note, source, created_at)
            VALUES(?, ?, ?, '由明细生成', '明细', ?)
            ON CONFLICT(account_id, as_of_date) DO UPDATE SET
                balance=excluded.balance, note=excluded.note, source=excluded.source
            """,
            (account_id, as_of, balance, stamp),
        )


def save_import_map(account_id, mapping):
    if remote := _remote():
        return remote.save_import_map(account_id, mapping)
    with connect() as conn:
        conn.execute(
            "UPDATE accounts SET import_map=? WHERE id=?",
            (json.dumps(mapping, ensure_ascii=False) if mapping else "", account_id),
        )
        _rebuild_balances(conn, account_id)


def import_txns(account_id, rows, mapping, batch, progress=None):
    """追加明细，已存在的（指纹相同）自动跳过。返回 (新增, 跳过)。"""
    if remote := _remote():
        return remote.import_txns(account_id, rows, mapping, batch, progress)
    stamp = now_text()
    with connect() as conn:
        if mapping is not None:
            conn.execute("UPDATE accounts SET import_map=? WHERE id=?", (json.dumps(mapping, ensure_ascii=False), account_id))
        before = conn.execute("SELECT COUNT(*) AS n FROM account_txns WHERE account_id=?", (account_id,)).fetchone()["n"]
        conn.executemany(
            """
            INSERT INTO account_txns(account_id, txn_date, txn_time, amount, balance, counterparty, summary,
                                     note, fingerprint, batch, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, fingerprint) DO NOTHING
            """,
            [
                (
                    account_id, row["txn_date"], row.get("txn_time") or "", r2(row["amount"]), r2(row.get("balance")),
                    row.get("counterparty") or "", row.get("summary") or "", row.get("note") or "",
                    row["fingerprint"], batch, stamp, stamp,
                )
                for row in rows
            ],
        )
        after = conn.execute("SELECT COUNT(*) AS n FROM account_txns WHERE account_id=?", (account_id,)).fetchone()["n"]
        _rebuild_balances(conn, account_id)
    return after - before, len(rows) - (after - before)


def list_txns(account_id):
    return _all(
        "SELECT * FROM account_txns WHERE account_id=? ORDER BY txn_date DESC, txn_time DESC, id DESC",
        (account_id,),
    )


def txn_batches(account_id):
    return _all(
        """
        SELECT batch, COUNT(*) AS n, MIN(txn_date) AS first, MAX(txn_date) AS last, MAX(created_at) AS at
        FROM account_txns WHERE account_id=?
        GROUP BY batch ORDER BY at DESC
        """,
        (account_id,),
    )


def _txn_fields(data):
    day, _clock = statement.parse_date(data.get("txn_date"))
    if not day:
        raise ValueError("交易日期请写成 YYYY-MM-DD")
    clock = statement.parse_time(data.get("txn_time")) if data.get("txn_time") else ""
    if data.get("amount") in (None, ""):
        raise ValueError("请填写金额")
    return (
        day, clock, r2(data["amount"]), r2(data.get("balance")),
        (data.get("counterparty") or "").strip(), (data.get("summary") or "").strip(), (data.get("note") or "").strip(),
    )


def save_txn(data, txn_id=None):
    """修改一笔明细；没有 txn_id 时在 data['account_id'] 下手工补一笔。"""
    if remote := _remote():
        return remote.save_txn(data, txn_id)
    fields = _txn_fields(data)
    stamp = now_text()
    with connect() as conn:
        if txn_id:
            row = conn.execute("SELECT account_id FROM account_txns WHERE id=?", (txn_id,)).fetchone()
            if row is None:
                raise ValueError("这笔明细不存在")
            conn.execute(
                """
                UPDATE account_txns SET txn_date=?, txn_time=?, amount=?, balance=?, counterparty=?, summary=?,
                    note=?, updated_at=? WHERE id=?
                """,
                (*fields, stamp, txn_id),
            )
            updated = dict(conn.execute("SELECT * FROM account_txns WHERE id=?", (txn_id,)).fetchone())
            payload = _expense_payload(updated, updated["treat"])
            conn.execute(
                "UPDATE expenses SET occurred_on=?, amount=?, payee=? WHERE txn_id=? AND source='明细'",
                (updated["txn_date"], payload["amount"], payload["payee"], txn_id),
            )
            _rebuild_balances(conn, row["account_id"])
            return txn_id
        account_id = int(data["account_id"])
        cur = conn.execute(
            """
            INSERT INTO account_txns(account_id, txn_date, txn_time, amount, balance, counterparty, summary,
                                     note, fingerprint, batch, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, '手工补录', ?, ?)
            """,
            (account_id, *fields, f"m-{uuid.uuid4()}", stamp, stamp),
        )
        _rebuild_balances(conn, account_id)
        return cur.lastrowid


def delete_txns(txn_ids):
    if remote := _remote():
        return remote.delete_txns(txn_ids)
    ids = [int(item) for item in txn_ids]
    if not ids:
        return
    with connect() as conn:
        marks = ", ".join("?" for _ in ids)
        owners = [row["account_id"] for row in conn.execute(f"SELECT DISTINCT account_id FROM account_txns WHERE id IN ({marks})", ids)]
        _unlink(conn, f"id IN ({marks})", ids)
        conn.execute(f"DELETE FROM account_txns WHERE id IN ({marks})", ids)
        for account_id in owners:
            _rebuild_balances(conn, account_id)


def delete_txn_batch(account_id, batch):
    if remote := _remote():
        return remote.delete_txn_batch(account_id, batch)
    with connect() as conn:
        _unlink(conn, "account_id=? AND batch=?", (account_id, batch))
        conn.execute("DELETE FROM account_txns WHERE account_id=? AND batch=?", (account_id, batch))
        _rebuild_balances(conn, account_id)


def _unlink(conn, where, args):
    """明细删掉时：由明细生成的支出一起删；合并过的手工支出只解除关联。"""
    inner = f"SELECT id FROM account_txns WHERE {where}"
    conn.execute(f"DELETE FROM expenses WHERE source='明细' AND txn_id IN ({inner})", tuple(args))
    conn.execute(f"UPDATE expenses SET txn_id=NULL WHERE txn_id IN ({inner})", tuple(args))


def _expense_payload(txn, treat):
    payee = (txn.get("counterparty") or txn.get("summary") or "")[:80]
    summary = txn.get("summary") or ""
    note = ("退款" + (f" · {summary}" if summary else "")) if treat == "退款" else summary
    return {"amount": r2(-float(txn["amount"])), "payee": payee, "note": note[:300]}


def list_rules():
    return _all("SELECT * FROM txn_rules ORDER BY id")


def delete_rules(rule_ids):
    ids = [int(item) for item in rule_ids]
    if not ids:
        return
    if remote := _remote():
        return remote.delete_rules(ids)
    with connect() as conn:
        conn.execute(f"DELETE FROM txn_rules WHERE id IN ({','.join('?' * len(ids))})", ids)


def classify_txns(items, rules=()):
    """items: [{id, treat, category_id, expense_id}]；rules: [{field, keyword, treat, category_id}]。"""
    if remote := _remote():
        return remote.classify_txns(items, rules)
    stamp = now_text()
    with connect() as conn:
        categories = {row["id"] for row in conn.execute("SELECT id FROM expense_categories")}
        used = set()
        for item in items:
            treat = item.get("treat") or ""
            if treat not in TREATS:
                raise ValueError("归类无效")
            row = conn.execute("SELECT * FROM account_txns WHERE id=?", (int(item["id"]),)).fetchone()
            if row is None:
                raise ValueError("明细不存在")
            txn = dict(row)
            if treat == "支出" and txn["amount"] >= 0:
                raise ValueError("收入不能归为支出")
            if treat == "退款" and txn["amount"] <= 0:
                raise ValueError("只有收入能记为退款")
            _unlink(conn, "id=?", (txn["id"],))
            category_id = None
            merge_id = int(item.get("expense_id") or 0)
            if treat == "支出" and merge_id:
                target = conn.execute("SELECT id, category_id, txn_id FROM expenses WHERE id=?", (merge_id,)).fetchone()
                if target is None or target["txn_id"] or merge_id in used:
                    raise ValueError("要合并的手工支出不存在或已经合并过")
                used.add(merge_id)
                category_id = target["category_id"]
                conn.execute("UPDATE expenses SET txn_id=? WHERE id=?", (txn["id"], merge_id))
            elif treat in ("支出", "退款"):
                category_id = int(item.get("category_id") or 0)
                if category_id not in categories:
                    raise ValueError("请选择支出类别")
                payload = _expense_payload(txn, treat)
                conn.execute(
                    """
                    INSERT INTO expenses(category_id, occurred_on, amount, payee, note, source, txn_id, created_at)
                    VALUES(?, ?, ?, ?, ?, '明细', ?, ?)
                    """,
                    (category_id, txn["txn_date"], payload["amount"], payload["payee"], payload["note"], txn["id"], stamp),
                )
            conn.execute(
                "UPDATE account_txns SET treat=?, category_id=?, updated_at=? WHERE id=?",
                (treat, category_id, stamp, txn["id"]),
            )
        for rule in rules:
            keyword = (rule.get("keyword") or "").strip()[:40]
            treat = rule.get("treat")
            if rule.get("field") not in ("counterparty", "summary") or not keyword or treat not in ("支出", "退款", "不计"):
                continue
            category_id = None if treat == "不计" else rule.get("category_id")
            if treat != "不计" and category_id not in categories:
                continue
            conn.execute(
                """
                INSERT INTO txn_rules(field, keyword, treat, category_id, created_at) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(field, keyword) DO UPDATE SET treat=excluded.treat, category_id=excluded.category_id
                """,
                (rule["field"], keyword, treat, category_id, stamp),
            )
    return {"classified": len(items)}


def pending_count():
    from gxyfi import classify

    return len(classify.pending(list_txns_all_open(), list_rules()))


def list_txns_all_open():
    return _all(
        """
        SELECT t.*, a.name AS account_name FROM account_txns t JOIN accounts a ON a.id = t.account_id
        WHERE t.treat='' ORDER BY t.txn_date DESC, t.id DESC
        """
    )


def txn_counts():
    rows = _all("SELECT account_id, COUNT(*) AS n, MAX(txn_date) AS last FROM account_txns GROUP BY account_id")
    return {row["account_id"]: row for row in rows}


def list_all_txns():
    return _all(
        """
        SELECT t.*, a.name AS account_name, a.currency
        FROM account_txns t JOIN accounts a ON a.id = t.account_id
        ORDER BY a.name, t.txn_date, t.txn_time, t.id
        """
    )


def list_all_balances():
    return _all(
        """
        SELECT b.*, a.name AS account_name, a.currency, a.institution
        FROM balance_entries b
        JOIN accounts a ON a.id = b.account_id
        ORDER BY b.as_of_date, a.name
        """
    )


def latest_account_rows():
    rows = []
    for account in list_accounts():
        latest = latest_balance(account["id"])
        rows.append(
            {
                **account,
                "balance": None if latest is None else latest["balance"],
                "as_of_date": None if latest is None else latest["as_of_date"],
                "balance_note": "" if latest is None else latest["note"],
            }
        )
    return rows


def list_holdings():
    return _all("SELECT * FROM holdings ORDER BY market, symbol, id")


def save_holding(data, holding_id=None):
    if remote := _remote():
        return remote.save_holding(data, holding_id)
    name = data["name"].strip()
    symbol = data["symbol"].strip()
    if not name or not symbol:
        raise ValueError("请填写名称和代码")
    quantity = r4(data["quantity"])
    if quantity is None or quantity <= 0:
        raise ValueError("数量需要大于 0")
    fields = (
        name,
        symbol,
        data["market"],
        quantity,
        data.get("currency") or "CNY",
        r4(data.get("avg_cost")),
        r4(data.get("manual_price")),
        data.get("account_label", "").strip(),
        data.get("note", "").strip(),
    )
    with connect() as conn:
        if holding_id:
            conn.execute(
                """
                UPDATE holdings
                SET name=?, symbol=?, market=?, quantity=?, currency=?,
                    avg_cost=?, manual_price=?, account_label=?, note=?
                WHERE id=?
                """,
                (*fields, holding_id),
            )
            return holding_id
        cur = conn.execute(
            """
            INSERT INTO holdings(
                name, symbol, market, quantity, currency, avg_cost, manual_price,
                account_label, note, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*fields, now_text()),
        )
        return cur.lastrowid


def delete_holding(holding_id):
    if remote := _remote():
        return remote.delete_holding(holding_id)
    with connect() as conn:
        conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))


def list_dca_plans():
    return _all("SELECT * FROM dca_plans ORDER BY status, symbol, id")


def list_dca_records(plan_id=None):
    if plan_id is None:
        return _all(
            """
            SELECT records.*, plans.name AS plan_name, plans.symbol AS plan_symbol
            FROM dca_records AS records
            JOIN dca_plans AS plans ON plans.id = records.plan_id
            ORDER BY records.trade_date, records.id
            """
        )
    return _all(
        "SELECT * FROM dca_records WHERE plan_id=? ORDER BY trade_date, id",
        (plan_id,),
    )


def _plan_or_raise(plan_id):
    plan = _one("SELECT * FROM dca_plans WHERE id=?", (plan_id,))
    if plan is None:
        raise ValueError("定投计划不存在")
    return plan


def save_dca_plan(data, plan_id=None):
    if remote := _remote():
        return remote.save_dca_plan(data, plan_id)
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("请填写基金名称")
    symbol = dca.normalize_symbol(data.get("symbol"))
    amount = r2(data.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("每期金额需要大于 0")
    cycle = data.get("cycle") or "每月"
    if cycle not in dca.CYCLES:
        raise ValueError("扣款周期不正确")
    debit_day = int(data.get("debit_day") or 0)
    limit = 28 if cycle == "每月" else 5
    if debit_day < 1 or debit_day > limit:
        raise ValueError("扣款日不在可选范围内")
    status = data.get("status") or "进行中"
    if status not in dca.STATUSES:
        raise ValueError("状态不正确")
    started_on = data.get("started_on") or None
    manual_nav = r4(data.get("manual_nav"))
    if manual_nav is not None and manual_nav <= 0:
        raise ValueError("最新净值需要大于 0")
    fields = (
        name,
        symbol,
        amount,
        cycle,
        debit_day,
        status,
        started_on,
        manual_nav,
        str(data.get("note") or "").strip(),
    )
    with connect() as conn:
        if plan_id:
            if conn.execute("SELECT 1 FROM dca_plans WHERE id=?", (plan_id,)).fetchone() is None:
                raise ValueError("定投计划不存在")
            conn.execute(
                """
                UPDATE dca_plans
                SET name=?, symbol=?, amount=?, cycle=?, debit_day=?, status=?,
                    started_on=?, manual_nav=?, note=?
                WHERE id=?
                """,
                (*fields, plan_id),
            )
            return plan_id
        cur = conn.execute(
            """
            INSERT INTO dca_plans(
                name, symbol, amount, cycle, debit_day, status, started_on, manual_nav, note, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*fields, now_text()),
        )
        return cur.lastrowid


def delete_dca_plan(plan_id):
    if remote := _remote():
        return remote.delete_dca_plan(plan_id)
    with connect() as conn:
        conn.execute("DELETE FROM dca_plans WHERE id=?", (plan_id,))


def _checked_records(plan_id, draft):
    _plan_or_raise(plan_id)
    dca.apply_position(draft)


def save_dca_record(data, record_id=None):
    if remote := _remote():
        return remote.save_dca_record(data, record_id)
    plan_id = data.get("plan_id")
    normalized = dca.normalize_record(data)
    existing = list_dca_records(plan_id)
    draft = []
    replaced = record_id is None
    for row in existing:
        if row["id"] == record_id:
            draft.append({**normalized, "id": record_id})
            replaced = True
        else:
            draft.append(row)
    if record_id is not None and not replaced:
        raise ValueError("确认记录不存在")
    if record_id is None:
        draft.append({**normalized, "id": 10**12})
    _checked_records(plan_id, draft)
    fields = (
        plan_id,
        normalized["kind"],
        normalized["trade_date"],
        normalized["amount"],
        normalized["nav"],
        normalized["shares"],
        normalized["fee"],
        normalized["note"],
    )
    with connect() as conn:
        if record_id:
            conn.execute(
                """
                UPDATE dca_records
                SET plan_id=?, kind=?, trade_date=?, amount=?, nav=?, shares=?, fee=?, note=?
                WHERE id=?
                """,
                (*fields, record_id),
            )
            return record_id
        cur = conn.execute(
            """
            INSERT INTO dca_records(
                plan_id, kind, trade_date, amount, nav, shares, fee, note, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*fields, now_text()),
        )
        return cur.lastrowid


def delete_dca_record(record_id):
    if remote := _remote():
        return remote.delete_dca_record(record_id)
    current = _one("SELECT * FROM dca_records WHERE id=?", (record_id,))
    if current is None:
        return
    remaining = [row for row in list_dca_records(current["plan_id"]) if row["id"] != record_id]
    dca.apply_position(remaining)
    with connect() as conn:
        conn.execute("DELETE FROM dca_records WHERE id=?", (record_id,))


def import_dca_records(plan_id, path):
    if remote := _remote():
        return remote.import_dca_records(plan_id, path)
    incoming = dca.read_xlsx(path)
    existing = list_dca_records(plan_id)
    draft = list(existing)
    for index, row in enumerate(incoming):
        draft.append({**row, "id": 10**12 + index})
    _checked_records(plan_id, draft)
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO dca_records(
                plan_id, kind, trade_date, amount, nav, shares, fee, note, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    plan_id,
                    row["kind"],
                    row["trade_date"],
                    row["amount"],
                    row["nav"],
                    row["shares"],
                    row["fee"],
                    row["note"],
                    now_text(),
                )
                for row in incoming
            ],
        )
    return len(incoming)


def dca_plan_rows():
    plans = list_dca_plans()
    grouped = {}
    for row in list_dca_records():
        grouped.setdefault(row["plan_id"], []).append(row)
    valued = []
    total = 0.0
    unpriced = 0
    for plan in plans:
        records = grouped.get(plan["id"], [])
        shares, cost = dca.apply_position(records)
        nav, source = dca.latest_nav(plan["manual_nav"], records)
        market_value = None if nav is None or shares <= 0 else round(shares * nav, 2)
        pnl = None if market_value is None else round(market_value - cost, 2)
        if shares > 0 and nav is None:
            unpriced += 1
        if market_value is not None:
            total += market_value
        valued.append(
            {
                **plan,
                "shares": shares,
                "cost": cost,
                "nav": nav,
                "nav_source": source,
                "market_value": market_value,
                "pnl": pnl,
                "cycle_label": dca.cycle_label(plan["cycle"], plan["debit_day"]),
                "records": len(records),
            }
        )
    return valued, round(total, 2), unpriced


def list_quotes():
    return _all("SELECT * FROM quotes ORDER BY market, symbol")


def save_quotes(quotes):
    if not quotes:
        return
    with connect() as conn:
        for quote in quotes:
            conn.execute(
                """
                INSERT INTO quotes(
                    market, symbol, name, price, prev_close, currency, quoted_at,
                    source, fetched_at, ok, message
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET
                    name=excluded.name,
                    price=excluded.price,
                    prev_close=excluded.prev_close,
                    currency=excluded.currency,
                    quoted_at=excluded.quoted_at,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at,
                    ok=excluded.ok,
                    message=excluded.message
                """,
                (
                    quote["market"],
                    quote["symbol"],
                    quote.get("name") or "",
                    quote.get("price"),
                    quote.get("prev_close"),
                    quote.get("currency") or "",
                    quote.get("quoted_at") or "",
                    quote.get("source") or "",
                    now_text(),
                    1 if quote.get("ok") else 0,
                    quote.get("message") or "",
                ),
            )


def list_properties():
    return _all("SELECT * FROM properties ORDER BY id")


def save_property(data, property_id=None):
    if remote := _remote():
        return remote.save_property(data, property_id)
    name = data["name"].strip()
    if not name:
        raise ValueError("请填写不动产名称")
    current_value = r2(data.get("current_value"))
    valued_on = data.get("valued_on") or None
    if current_value is not None and not valued_on:
        valued_on = date.today().isoformat()
    fields = (
        name,
        data.get("kind") or "住宅",
        data.get("city", "").strip(),
        data.get("address", "").strip(),
        r2(data.get("area_sqm")),
        data.get("acquired_on") or None,
        r2(data.get("purchase_price")),
        current_value,
        valued_on,
        r2(data.get("mortgage_balance") or 0) or 0,
        r2(data.get("monthly_rent") or 0) or 0,
        data.get("note", "").strip(),
    )
    with connect() as conn:
        if property_id:
            conn.execute(
                """
                UPDATE properties SET
                    name=?, kind=?, city=?, address=?, area_sqm=?, acquired_on=?,
                    purchase_price=?, current_value=?, valued_on=?, mortgage_balance=?,
                    monthly_rent=?, note=?
                WHERE id=?
                """,
                (*fields, property_id),
            )
            return property_id
        cur = conn.execute(
            """
            INSERT INTO properties(
                name, kind, city, address, area_sqm, acquired_on, purchase_price,
                current_value, valued_on, mortgage_balance, monthly_rent, note, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*fields, now_text()),
        )
        return cur.lastrowid


def delete_property(property_id):
    if remote := _remote():
        return remote.delete_property(property_id)
    with connect() as conn:
        conn.execute("DELETE FROM properties WHERE id=?", (property_id,))


def list_categories():
    return _all("SELECT * FROM expense_categories ORDER BY sort_order, id")


def save_category(name):
    if remote := _remote():
        return remote.save_category(name)
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("请填写类别名称")
    with connect() as conn:
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM expense_categories").fetchone()["n"]
        try:
            cur = conn.execute(
                "INSERT INTO expense_categories(name, sort_order) VALUES(?, ?)",
                (cleaned, order),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("这个类别已经存在") from exc
        return cur.lastrowid


def list_expenses():
    return _all(
        """
        SELECT e.*, c.name AS category
        FROM expenses e
        JOIN expense_categories c ON c.id = e.category_id
        ORDER BY e.occurred_on DESC, e.id DESC
        """
    )


def save_expense(data, expense_id=None):
    if remote := _remote():
        return remote.save_expense(data, expense_id)
    amount = r2(data["amount"])
    if amount is None or amount <= 0:
        raise ValueError("支出金额需要大于 0")
    fields = (
        int(data["category_id"]),
        data["occurred_on"],
        amount,
        data.get("payee", "").strip(),
        data.get("note", "").strip(),
    )
    with connect() as conn:
        if expense_id:
            conn.execute(
                """
                UPDATE expenses
                SET category_id=?, occurred_on=?, amount=?, payee=?, note=?
                WHERE id=?
                """,
                (*fields, expense_id),
            )
            conn.execute(
                """
                UPDATE account_txns SET category_id=?, updated_at=?
                WHERE id=(SELECT txn_id FROM expenses WHERE id=?)
                """,
                (fields[0], now_text(), expense_id),
            )
            return expense_id
        cur = conn.execute(
            """
            INSERT INTO expenses(category_id, occurred_on, amount, payee, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (*fields, now_text()),
        )
        return cur.lastrowid


def delete_expense(expense_id):
    if remote := _remote():
        return remote.delete_expense(expense_id)
    with connect() as conn:
        conn.execute(
            """
            UPDATE account_txns SET treat='不计', category_id=NULL, updated_at=?
            WHERE id=(SELECT txn_id FROM expenses WHERE id=?)
            """,
            (now_text(), expense_id),
        )
        conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))


def budget_progress(year):
    spent = {
        row["category_id"]: row["amount"]
        for row in _all(
            """
            SELECT category_id, SUM(amount) AS amount
            FROM expenses
            WHERE occurred_on >= ? AND occurred_on < ?
            GROUP BY category_id
            """,
            (f"{year}-01-01", f"{year + 1}-01-01"),
        )
    }
    budgets = {row["category_id"]: row for row in _all("SELECT * FROM budget_lines WHERE year=?", (year,))}
    rows = []
    for category in list_categories():
        planned = budgets.get(category["id"], {}).get("amount", 0) or 0
        actual = spent.get(category["id"], 0) or 0
        rows.append(
            {
                "category_id": category["id"],
                "category": category["name"],
                "planned": planned,
                "actual": actual,
                "remain": planned - actual,
                "ratio": None if planned == 0 else actual / planned,
                "note": budgets.get(category["id"], {}).get("note", ""),
            }
        )
    return rows


def save_budget(year, category_id, amount, note=""):
    if remote := _remote():
        return remote.save_budget(year, category_id, amount, note)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO budget_lines(year, category_id, amount, note)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(year, category_id) DO UPDATE SET
                amount=excluded.amount,
                note=excluded.note
            """,
            (int(year), int(category_id), r2(amount) or 0, note.strip()),
        )


def copy_budget(from_year, to_year):
    source = _all("SELECT * FROM budget_lines WHERE year=?", (from_year,))
    if not source:
        raise ValueError(f"{from_year} 年还没有预算")
    for row in source:
        save_budget(to_year, row["category_id"], row["amount"], row["note"])
    return len(source)


def list_budgets():
    return _all(
        """
        SELECT b.*, c.name AS category
        FROM budget_lines b
        JOIN expense_categories c ON c.id = b.category_id
        ORDER BY b.year, c.sort_order
        """
    )


def _month_key(year, month):
    return f"{year:04d}-{month:02d}"


def recent_months(count, today=None):
    today = today or date.today()
    year, month = today.year, today.month
    found = []
    for _ in range(count):
        found.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(found))


def expense_total(start, end):
    row = _one(
        "SELECT COALESCE(SUM(amount), 0) AS amount FROM expenses WHERE occurred_on>=? AND occurred_on<?",
        (start, end),
    )
    return row["amount"] or 0


def monthly_expense_series(count=6, today=None):
    series = []
    for year, month in recent_months(count, today):
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        series.append((_month_key(year, month), expense_total(start.isoformat(), end.isoformat())))
    return series


def valued_holdings(rates=None):
    rates = fx_rates() if rates is None else rates
    quotes = {(row["market"], row["symbol"]): row for row in list_quotes()}
    valued = []
    missing = False
    total = 0.0
    for holding in list_holdings():
        quote = quotes.get((holding["market"], holding["symbol"]))
        if holding["manual_price"] is not None:
            price = holding["manual_price"]
            source = "手工"
            prev = None if quote is None else quote["prev_close"]
            quoted_at = ""
            quote_name = holding["name"]
        elif quote and quote["ok"] and quote["price"] is not None:
            price = quote["price"]
            source = quote["source"]
            prev = quote["prev_close"]
            quoted_at = quote["quoted_at"]
            quote_name = quote["name"] or holding["name"]
        else:
            price = None
            source = "" if quote is None else (quote["message"] or "未取得行情")
            prev = None
            quoted_at = ""
            quote_name = holding["name"]
        market_value = None if price is None else price * holding["quantity"]
        cost = None if holding["avg_cost"] is None else holding["avg_cost"] * holding["quantity"]
        pnl = None if market_value is None or cost is None else market_value - cost
        cny = None if market_value is None else to_cny(market_value, holding["currency"], rates)
        cost_cny = None if cost is None else to_cny(cost, holding["currency"], rates)
        pnl_cny = None if pnl is None else to_cny(pnl, holding["currency"], rates)
        ret = None if pnl is None or not cost else pnl / cost
        if market_value is not None and cny is None:
            missing = True
        if cny is not None:
            total += cny
        day = None
        if price is not None and prev not in (None, 0):
            day = (price - prev) / prev
        valued.append(
            {
                **holding,
                "price": price,
                "source": source,
                "prev_close": prev,
                "quoted_at": quoted_at,
                "quote_name": quote_name,
                "market_value": market_value,
                "cost": cost,
                "pnl": pnl,
                "cny": cny,
                "cost_cny": cost_cny,
                "pnl_cny": pnl_cny,
                "ret": ret,
                "day": day,
            }
        )
    for row in valued:
        row["weight"] = None if row["cny"] is None or not total else row["cny"] / total
    return valued, total, missing


def holdings_totals(rows):
    """只合计填了成本、也有价格的持仓，收益率才有意义。"""
    priced = [row for row in rows if row["cny"] is not None and row["cost_cny"] is not None]
    cost = sum(row["cost_cny"] for row in priced)
    value = sum(row["cny"] for row in priced)
    return {
        "value": sum(row["cny"] for row in rows if row["cny"] is not None),
        "cost": cost,
        "pnl": value - cost,
        "ret": None if not cost else (value - cost) / cost,
        "without_cost": sum(1 for row in rows if row["cny"] is not None and row["cost_cny"] is None),
    }


def portfolio_summary(today=None):
    today = today or date.today()
    rates = fx_rates()
    accounts = []
    savings = 0.0
    savings_missing = False
    stale = []
    for row in latest_account_rows():
        cny = None if row["balance"] is None else to_cny(row["balance"], row["currency"], rates)
        if row["balance"] is not None and cny is None:
            savings_missing = True
        if cny is not None:
            savings += cny
        age = None
        if row["as_of_date"]:
            age = (today - date.fromisoformat(row["as_of_date"])).days
            if age > STALE_DAYS:
                stale.append({**row, "age": age})
        accounts.append({**row, "cny": cny, "age": age})
    holdings, invest, invest_missing = valued_holdings(rates)
    dca_rows, dca_total, dca_unpriced = dca_plan_rows()
    invest += dca_total
    properties = []
    equity_total = 0.0
    rent_total = 0.0
    property_missing = False
    for item in list_properties():
        equity = None
        if item["current_value"] is not None:
            equity = item["current_value"] - (item["mortgage_balance"] or 0)
            equity_total += equity
        else:
            property_missing = True
        rent_total += item["monthly_rent"] or 0
        properties.append({**item, "equity": equity})
    month_start = today.replace(day=1)
    if today.month == 12:
        month_end = date(today.year + 1, 1, 1)
    else:
        month_end = date(today.year, today.month + 1, 1)
    year_start = date(today.year, 1, 1)
    year_end = date(today.year + 1, 1, 1)
    budgets = budget_progress(today.year)
    planned = sum(row["planned"] for row in budgets)
    actual_year = expense_total(year_start.isoformat(), year_end.isoformat())
    return {
        "accounts": accounts,
        "savings_cny": savings,
        "savings_missing": savings_missing,
        "holdings": holdings,
        "dca_plans": dca_rows,
        "invest_cny": invest,
        "invest_missing": invest_missing,
        "unpriced": sum(1 for row in holdings if row["price"] is None) + dca_unpriced,
        "properties": properties,
        "property_equity_cny": equity_total,
        "property_missing": property_missing,
        "rent_monthly": rent_total,
        "month_expense": expense_total(month_start.isoformat(), month_end.isoformat()),
        "year_expense": actual_year,
        "budget_total": planned,
        "budget_rows": budgets,
        "budget_ratio": None if planned == 0 else actual_year / planned,
        "monthly_expenses": monthly_expense_series(6, today),
        "stale_accounts": stale,
        "fx": rates,
        "today": today.isoformat(),
    }
