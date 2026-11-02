"""与网站、手机共用一本云端台账。

连上以后，本机数据库是云端的副本：每次修改先发到网站，成功后用网站返回的整本账覆盖本机。
网站返回的记录编号直接作为本机编号，页面上的选择和编辑因此不用换算。
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from http.cookies import SimpleCookie

from gxyfi import db, dca

DEFAULT_URL = "https://gxyfi-web.catanddogmiaomiao.workers.dev"
USER_AGENT = "gxyFi-desktop/0.2 (Windows)"
TIMEOUT = 30
WRITES = 0
LAST_RESULT = {}
UPLOAD_BYTES = 60_000
EXPIRED_TEXT = "网页端登录已过期，请点右上角「网页同步」重新登录。这次修改没有保存。"


class CloudError(ValueError):
    pass


class AuthExpired(CloudError):
    pass


def config():
    return {
        "url": db.get_setting("cloud_url") or DEFAULT_URL,
        "user": db.get_setting("cloud_user") or "",
        "token": db.get_setting("cloud_token") or "",
        "synced_at": db.get_setting("cloud_synced_at") or "",
    }


def enabled():
    return bool(db.get_setting("cloud_user"))


def expired():
    return enabled() and not db.get_setting("cloud_token")


def server_url(text):
    raw = (text or "").strip()
    if not raw:
        raise CloudError("请填写网页端地址")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    host = parts.hostname or ""
    local = host in ("localhost", "127.0.0.1") or host.endswith(".local")
    if not host:
        raise CloudError("网页端地址无效")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise CloudError("网页端地址需要以 https:// 开头")
    return f"{parts.scheme}://{parts.netloc}"


def mark_expired():
    db.set_setting("cloud_token", "")


def disconnect():
    cfg = config()
    if cfg["token"]:
        try:
            _request(cfg["url"], "/api/logout", cfg["token"], body={})
        except CloudError:
            pass
    db.set_setting("cloud_token", "")
    db.set_setting("cloud_user", "")
    db.set_setting("cloud_synced_at", "")
    db.clear_records()


def _request(url, path, token="", body=None, method=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url.rstrip("/") + path, data=data, method=method or ("POST" if data else "GET"))
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            return response.headers, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("error") or ""
        except Exception:
            message = ""
        if exc.code == 401 and path != "/api/login":
            raise AuthExpired(EXPIRED_TEXT) from None
        raise CloudError(message or f"网站返回错误 {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudError("连不上网站，这次修改没有保存。请检查网络后再试。") from exc
    except json.JSONDecodeError as exc:
        raise CloudError("网站返回的内容无法识别") from exc


def login(url, username, password):
    headers, _payload = _request(url, "/api/login", body={"username": username, "password": password})
    jar = SimpleCookie()
    for value in headers.get_all("Set-Cookie") or []:
        jar.load(value)
    morsel = jar.get("gxyfi_session")
    if morsel is None or not morsel.value:
        raise CloudError("网站没有返回登录凭据")
    return morsel.value


def with_txns(state, cfg, force=False):
    """账户明细不放在整本账里；网页端的明细版本变了才另外下载。"""
    rev = state.get("txnRev")
    if rev is not None and (force or rev != db.get_setting("txn_rev")):
        _headers, payload = _request(cfg["url"], "/api/txns", cfg["token"])
        state["txns"] = payload.get("txns") or []
    return state


def fetch_state(cfg=None, force_txns=False):
    cfg = cfg or config()
    _headers, state = _request(cfg["url"], "/api/state", cfg["token"])
    return with_txns(state, cfg, force_txns)


def fetch_quotes(cfg=None):
    cfg = cfg or config()
    _headers, state = _request(cfg["url"], "/api/quotes/refresh", cfg["token"], body={})
    return with_txns(state, cfg)


def apply(state):
    db.replace_from_cloud(state)
    db.set_setting("cloud_synced_at", datetime.now().strftime("%Y-%m-%d %H:%M"))


def save_login(url, username, token):
    db.set_setting("cloud_url", url)
    db.set_setting("cloud_user", username)
    db.set_setting("cloud_token", token)


def backup_local():
    source = db.db_path()
    target = source.with_name(f"gxyfi-本机备份-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(source, target)
    return target


@contextmanager
def _busy():
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
    except ImportError:
        app = None
    if app is not None:
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        if app is not None:
            QGuiApplication.restoreOverrideCursor()


def _ids(table):
    return {row["id"] for row in db._all(f"SELECT id FROM {table}")}


def mutate(entity, op, data=None, record_id=None, table=None, pull_txns=True):
    global WRITES
    WRITES += 1
    before = _ids(table) if table and op == "create" else set()
    cfg = config()
    if not cfg["token"]:
        raise AuthExpired(EXPIRED_TEXT)
    body = {"entity": entity, "op": op, "data": data or {}}
    if record_id is not None:
        body["id"] = record_id
    global LAST_RESULT
    try:
        with _busy():
            _headers, state = _request(cfg["url"], "/api/mutate", cfg["token"], body=body)
            if pull_txns:
                with_txns(state, cfg)
    except AuthExpired:
        mark_expired()
        raise
    LAST_RESULT = state.pop("result", None) or {}
    apply(state)
    if not table:
        return record_id
    if op != "create":
        return record_id
    created = _ids(table) - before
    return max(created) if created else None


def _op(record_id):
    return "update" if record_id else "create"


def save_account(data, account_id=None):
    payload = {key: data.get(key, "") for key in ("name", "institution", "kind", "currency", "note")}
    return mutate("account", _op(account_id), payload, account_id, "accounts")


def delete_account(account_id):
    mutate("account", "delete", record_id=account_id)


def _balance_id(account_id, as_of):
    row = db._one("SELECT id FROM balance_entries WHERE account_id=? AND as_of_date=?", (account_id, as_of))
    return None if row is None else row["id"]


def save_balance(data, entry_id=None):
    account_id = int(data["account_id"])
    as_of = data["as_of_date"]
    payload = {"account_id": account_id, "as_of_date": as_of, "balance": data["balance"], "note": data.get("note", "")}
    if entry_id:
        old = db._one("SELECT * FROM balance_entries WHERE id=?", (entry_id,))
        moved = old is not None and (old["account_id"] != account_id or old["as_of_date"] != as_of)
        if moved and _balance_id(account_id, as_of) is not None:
            raise ValueError("这个账户在这一天已经有一条余额")
        mutate("balance", "create", payload)
        if moved:
            mutate("balance", "delete", record_id=entry_id)
        return _balance_id(account_id, as_of)
    mutate("balance", "create", payload)
    return _balance_id(account_id, as_of)


def delete_balance(entry_id):
    mutate("balance", "delete", record_id=entry_id)


HOLDING_KEYS = ("name", "symbol", "market", "quantity", "currency", "avg_cost", "manual_price", "account_label", "note")


def save_holding(data, holding_id=None):
    payload = {key: data.get(key) for key in HOLDING_KEYS}
    return mutate("holding", _op(holding_id), payload, holding_id, "holdings")


def delete_holding(holding_id):
    mutate("holding", "delete", record_id=holding_id)


PLAN_KEYS = ("name", "symbol", "amount", "cycle", "debit_day", "status", "started_on", "manual_nav", "note")
RECORD_KEYS = ("plan_id", "kind", "trade_date", "amount", "nav", "shares", "fee", "note")


def save_dca_plan(data, plan_id=None):
    payload = {key: data.get(key) for key in PLAN_KEYS}
    return mutate("dca_plan", _op(plan_id), payload, plan_id, "dca_plans")


def delete_dca_plan(plan_id):
    mutate("dca_plan", "delete", record_id=plan_id)


def save_dca_record(data, record_id=None):
    payload = {key: data.get(key) for key in RECORD_KEYS}
    return mutate("dca_record", _op(record_id), payload, record_id, "dca_records")


def delete_dca_record(record_id):
    mutate("dca_record", "delete", record_id=record_id)


def _cell(value):
    if value is None:
        return ""
    text = str(value)
    for mark in ("\t", ",", "，", "\r", "\n"):
        text = text.replace(mark, " ")
    return text


def import_rows_text(rows):
    lines = ["\t".join(dca.TEMPLATE_HEADERS)]
    for row in rows:
        lines.append(
            "\t".join(
                _cell(row.get(key))
                for key in ("trade_date", "kind", "amount", "nav", "shares", "fee", "note")
            )
        )
    return "\n".join(lines)


def import_dca_records(plan_id, path):
    rows = dca.read_xlsx(path)
    mutate("dca_import", "create", {"plan_id": plan_id, "text": import_rows_text(rows)})
    return len(rows)


PROPERTY_KEYS = (
    "name", "kind", "city", "address", "area_sqm", "acquired_on", "purchase_price",
    "current_value", "valued_on", "mortgage_balance", "monthly_rent", "note",
)


def save_property(data, property_id=None):
    payload = {key: data.get(key) for key in PROPERTY_KEYS}
    return mutate("property", _op(property_id), payload, property_id, "properties")


def delete_property(property_id):
    mutate("property", "delete", record_id=property_id)


def save_category(name):
    return mutate("category", "create", {"name": name}, None, "expense_categories")


def save_expense(data, expense_id=None):
    payload = {key: data.get(key) for key in ("category_id", "occurred_on", "amount", "payee", "note")}
    return mutate("expense", _op(expense_id), payload, expense_id, "expenses")


def delete_expense(expense_id):
    mutate("expense", "delete", record_id=expense_id)


def save_budget(year, category_id, amount, note=""):
    mutate("budget", "create", {"year": int(year), "category_id": int(category_id), "amount": amount or 0, "note": note})


def save_fx_rates(rates):
    for code, item in rates.items():
        if item.get("rate") is not None:
            mutate("fx", "create", {"code": code, "rate": item["rate"]})


TXN_KEYS = ("txn_date", "txn_time", "amount", "balance", "counterparty", "summary", "note")


def save_import_map(account_id, mapping):
    mutate("account_map", "update", {"map": mapping}, account_id)


def _chunks(rows):
    chunk, size = [], 0
    for row in rows:
        length = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 1
        if chunk and (size + length > UPLOAD_BYTES or len(chunk) >= 400):
            yield chunk
            chunk, size = [], 0
        chunk.append(row)
        size += length
    if chunk:
        yield chunk


def import_txns(account_id, rows, mapping, batch, progress=None):
    payload = [{**{key: row.get(key) for key in TXN_KEYS}, "fingerprint": row["fingerprint"]} for row in rows]
    added = skipped = 0
    chunks = list(_chunks(payload)) or [[]]
    sent = 0
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        data = {"account_id": account_id, "batch": batch, "rows": chunk, "final": last}
        if index == 0 and mapping is not None:
            data["map"] = mapping
        mutate("txn_import", "create", data, pull_txns=last)
        sent += len(chunk)
        if progress is not None:
            progress(sent, len(payload))
        added += int(LAST_RESULT.get("added") or 0)
        skipped += int(LAST_RESULT.get("skipped") or 0)
    return added, skipped


def save_txn(data, txn_id=None):
    payload = {key: data.get(key) for key in TXN_KEYS}
    if txn_id:
        mutate("txn", "update", payload, txn_id)
        return txn_id
    payload["account_id"] = int(data["account_id"])
    return mutate("txn", "create", payload, None, "account_txns")


def delete_txns(txn_ids):
    ids = [int(item) for item in txn_ids]
    for start in range(0, len(ids), 2000):
        mutate("txn", "delete", {"ids": ids[start:start + 2000]})


def delete_txn_batch(account_id, batch):
    mutate("txn_batch", "delete", {"account_id": account_id, "batch": batch})


def delete_rules(rule_ids):
    ids = [int(item) for item in rule_ids]
    for start in range(0, len(ids), 500):
        mutate("txn_rule", "delete", {"ids": ids[start:start + 500]}, pull_txns=False)


def classify_txns(items, rules=()):
    items, rules = list(items), list(rules)
    chunks = [items[start:start + 200] for start in range(0, len(items), 200)] or [[]]
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        mutate("txn_classify", "update", {"items": chunk, "rules": rules if last else []}, pull_txns=last)
    return {"classified": len(items)}


def _money(value):
    return "—" if value is None else f"¥{float(value):,.2f}"


def _latest(balances, account_id):
    rows = [row for row in balances if row["account_id"] == account_id]
    return max(rows, key=lambda row: (row["as_of_date"], row["id"])) if rows else None


def local_only(local, remote):
    """找出本机有、网页端没有的记录。返回待选清单和已对上的账户、定投计划编号。"""
    matches = {"accounts": {}, "plans": {}}
    items = []

    def add(kind, section, label, row, checked=True):
        items.append({"kind": kind, "section": section, "label": label, "row": row, "checked": checked})

    cloud_accounts = list(remote.get("accounts") or [])
    cloud_balances = remote.get("balances") or []
    taken = set()
    for account in local["accounts"]:
        found = next((row for row in cloud_accounts if row["name"] == account["name"] and row["id"] not in taken), None)
        if found is None:
            mine = _latest(local["balances"], account["id"])
            for row in cloud_accounts:
                theirs = _latest(cloud_balances, row["id"])
                if (
                    row["id"] not in taken
                    and mine is not None
                    and theirs is not None
                    and row["currency"] == account["currency"]
                    and abs(float(mine["balance"]) - float(theirs["balance"])) < 0.005
                ):
                    found = row
                    break
        if found is not None:
            taken.add(found["id"])
            matches["accounts"][account["id"]] = found["id"]
        else:
            add("account", "账户", f"{account['name']}  {account['institution']}".strip(), account)
    names = {row["id"]: row["name"] for row in local["accounts"]}
    cloud_days = {(row["account_id"], row["as_of_date"]) for row in cloud_balances}
    for row in sorted(local["balances"], key=lambda item: (item["account_id"], item["as_of_date"])):
        if row.get("source") == "明细":
            continue
        cloud_id = matches["accounts"].get(row["account_id"])
        if cloud_id is not None and (cloud_id, row["as_of_date"]) in cloud_days:
            continue
        label = f"{names.get(row['account_id'], '')}  {row['as_of_date']}  {_money(row['balance'])}"
        add("balance", "余额快照", label, row, checked=cloud_id is None)

    cloud_properties = {row["name"] for row in remote.get("properties") or []}
    for row in local["properties"]:
        if row["name"] not in cloud_properties:
            add("property", "不动产", f"{row['name']}  {row['city']}  现值 {_money(row['current_value'])}", row)

    cloud_holdings = {(row["market"], row["symbol"]) for row in remote.get("holdings") or []}
    for row in local["holdings"]:
        if (row["market"], row["symbol"]) not in cloud_holdings:
            add("holding", "投资持仓", f"{row['name']}  {row['symbol']}  数量 {row['quantity']:g}", row)

    local_categories = {row["id"]: row["name"] for row in local["categories"]}
    cloud_categories = {row["id"]: row["name"] for row in remote.get("categories") or []}
    cloud_expenses = {
        (row["occurred_on"], round(float(row["amount"]), 2), cloud_categories.get(row["category_id"]), row["payee"])
        for row in remote.get("expenses") or []
    }
    for row in local["expenses"]:
        key = (row["occurred_on"], round(float(row["amount"]), 2), local_categories.get(row["category_id"]), row["payee"])
        if key not in cloud_expenses:
            add("expense", "支出", f"{row['occurred_on']}  {key[2]}  {_money(row['amount'])}  {row['payee']}".strip(), row)

    cloud_budgets = {(row["year"], cloud_categories.get(row["category_id"])) for row in remote.get("budgets") or []}
    for row in local["budgets"]:
        name = local_categories.get(row["category_id"])
        if (row["year"], name) not in cloud_budgets:
            add("budget", "预算", f"{row['year']}  {name}  {_money(row['amount'])}", row)

    cloud_plans = {row["symbol"]: row["id"] for row in remote.get("dcaPlans") or []}
    for row in local["dcaPlans"]:
        if row["symbol"] in cloud_plans:
            matches["plans"][row["id"]] = cloud_plans[row["symbol"]]
        else:
            add("dca_plan", "基金定投", f"{row['name']}  {row['symbol']}  每期 {_money(row['amount'])}", row)
    cloud_records = {
        (row["plan_id"], row["trade_date"], row["kind"], round(float(row["shares"]), 4))
        for row in remote.get("dcaRecords") or []
    }
    plan_names = {row["id"]: row["name"] for row in local["dcaPlans"]}
    for row in sorted(local["dcaRecords"], key=lambda item: (item["trade_date"], item["id"])):
        cloud_plan = matches["plans"].get(row["plan_id"])
        if cloud_plan is not None and (cloud_plan, row["trade_date"], row["kind"], round(float(row["shares"]), 4)) in cloud_records:
            continue
        label = f"{plan_names.get(row['plan_id'], '')}  {row['trade_date']}  {row['kind']}  {row['shares']:g} 份"
        add("dca_record", "定投记录", label, row)

    groups = {}
    for row in local.get("txns") or []:
        groups.setdefault(row["account_id"], []).append(row)
    for account_id, rows in groups.items():
        label = f"{names.get(account_id, '')}  {len(rows)} 笔  {rows[0]['txn_date']} ~ {rows[-1]['txn_date']}"
        add("txns", "账户明细（网页上已有的会自动跳过）", label, {"account_id": account_id, "rows": rows})
    return items, matches


UPLOAD_ORDER = ("account", "balance", "property", "holding", "expense", "budget", "dca_plan", "dca_record", "txns")


def upload(selected, local, matches):
    """把勾选的本机记录追加到已登录的网页账号。调用前本机已经换成网页端的副本。"""
    account_ids = dict(matches["accounts"])
    plan_ids = dict(matches["plans"])
    category_names = {row["id"]: row["name"] for row in local["categories"]}

    def category_id(local_id):
        name = category_names.get(local_id)
        if not name:
            return None
        row = db._one("SELECT id FROM expense_categories WHERE name=?", (name,))
        return row["id"] if row else save_category(name)

    sent = 0
    for kind in UPLOAD_ORDER:
        for item in (entry for entry in selected if entry["kind"] == kind):
            row = item["row"]
            if kind == "account":
                account_ids[row["id"]] = save_account(row)
            elif kind == "balance":
                target = account_ids.get(row["account_id"])
                if target is None:
                    continue
                mutate("balance", "create", {
                    "account_id": target, "as_of_date": row["as_of_date"], "balance": row["balance"], "note": row["note"],
                })
            elif kind == "property":
                save_property(row)
            elif kind == "holding":
                save_holding(row)
            elif kind == "expense":
                target = category_id(row["category_id"])
                if target is None:
                    continue
                save_expense({**row, "category_id": target})
            elif kind == "budget":
                target = category_id(row["category_id"])
                if target is None:
                    continue
                save_budget(row["year"], target, row["amount"], row["note"])
            elif kind == "dca_plan":
                plan_ids[row["id"]] = save_dca_plan(row)
            elif kind == "dca_record":
                target = plan_ids.get(row["plan_id"])
                if target is None:
                    continue
                save_dca_record({**row, "plan_id": target})
            elif kind == "txns":
                target = account_ids.get(row["account_id"])
                if target is None:
                    continue
                source = next((item for item in local["accounts"] if item["id"] == row["account_id"]), {})
                mapping = json.loads(source["import_map"]) if source.get("import_map") else None
                import_txns(target, row["rows"], mapping, "首次登录上传")
            sent += 1
    return sent
