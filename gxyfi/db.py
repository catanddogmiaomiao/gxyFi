"""本机 SQLite 台账。桌面版是单用户，数据不上传。"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

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
"""


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
    with connect() as conn:
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
    account_id = int(data["account_id"])
    as_of = data["as_of_date"]
    balance = r2(data["balance"])
    note = data.get("note", "").strip()
    with connect() as conn:
        if entry_id:
            try:
                conn.execute(
                    """
                    UPDATE balance_entries
                    SET account_id=?, as_of_date=?, balance=?, note=?
                    WHERE id=?
                    """,
                    (account_id, as_of, balance, note, entry_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("这个账户在这一天已经有一条余额") from exc
            return entry_id
        conn.execute(
            """
            INSERT INTO balance_entries(account_id, as_of_date, balance, note, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(account_id, as_of_date) DO UPDATE SET
                balance=excluded.balance,
                note=excluded.note
            """,
            (account_id, as_of, balance, note, now_text()),
        )
        row = conn.execute(
            "SELECT id FROM balance_entries WHERE account_id=? AND as_of_date=?",
            (account_id, as_of),
        ).fetchone()
        return row["id"]


def delete_balance(entry_id):
    with connect() as conn:
        conn.execute("DELETE FROM balance_entries WHERE id=?", (entry_id,))


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
    with connect() as conn:
        conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))


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
    with connect() as conn:
        conn.execute("DELETE FROM properties WHERE id=?", (property_id,))


def list_categories():
    return _all("SELECT * FROM expense_categories ORDER BY sort_order, id")


def save_category(name):
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
    with connect() as conn:
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
        pnl_cny = None if pnl is None else to_cny(pnl, holding["currency"], rates)
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
                "pnl_cny": pnl_cny,
                "day": day,
            }
        )
    return valued, total, missing


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
        "invest_cny": invest,
        "invest_missing": invest_missing,
        "unpriced": sum(1 for row in holdings if row["price"] is None),
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
