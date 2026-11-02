"""银行、支付平台导出的账户明细：读表、认表头、猜字段、按映射解析成交易。

映射按列名记，不按列号记，所以银行调换列的顺序不影响；列名变了才需要重新确认。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

FIELDS = (
    ("txn_date", "交易日期"),
    ("txn_time", "交易时间"),
    ("amount", "金额（正负）"),
    ("income", "收入金额"),
    ("expense", "支出金额"),
    ("direction", "收支方向"),
    ("balance", "交易后余额"),
    ("counterparty", "对方户名"),
    ("summary", "摘要 / 用途"),
    ("note", "备注"),
)
FIELD_LABEL = dict(FIELDS)
AMOUNT_MODES = (
    ("signed", "一列金额，收入为正、支出为负"),
    ("split", "收入、支出分成两列"),
    ("direction", "一列金额，另有一列写收 / 支"),
)
MODE_FIELDS = {"signed": ("amount",), "split": ("income", "expense"), "direction": ("amount", "direction")}

ALIASES = {
    "txn_date": ("交易日期", "记账日期", "入账日期", "交易时间", "发生日期", "日期", "交易日", "记账时间", "date"),
    "txn_time": ("交易时间", "时间", "time"),
    "amount": ("交易金额", "金额", "发生额", "收支金额", "入账金额", "金额(元)", "amount"),
    "income": ("收入金额", "收入", "存入金额", "存入", "贷方金额", "贷方发生额", "贷方", "转入金额", "转入"),
    "expense": ("支出金额", "支出", "取出金额", "取出", "借方金额", "借方发生额", "借方", "转出金额", "转出"),
    "direction": ("收/支", "收支", "收支类型", "借贷标志", "借贷方向", "借贷", "方向", "交易方向"),
    "balance": ("交易后余额", "账户余额", "余额", "联机余额", "可用余额", "当前余额", "balance"),
    "counterparty": ("对方户名", "对方账户名", "对方名称", "对方账户名称", "交易对方", "对手方", "对方", "收款方", "付款方", "商户名称", "商户"),
    "summary": ("摘要", "交易摘要", "用途", "交易用途", "业务摘要", "交易类型", "业务类型", "商品说明", "商品", "交易说明", "附言"),
    "note": ("备注", "附言", "说明"),
}
GUESS_ORDER = ("txn_date", "balance", "income", "expense", "amount", "direction", "txn_time", "counterparty", "summary", "note")
INCOME_WORDS = ("收入", "收", "贷", "贷方", "存入", "转入", "入账", "入", "+", "c", "cr", "credit")
EXPENSE_WORDS = ("支出", "支", "借", "借方", "取出", "转出", "出账", "出", "-", "d", "dr", "debit")
DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})(?:[ T]*(\d{2}):?(\d{2}):?(\d{2})?)?$"),
)
TIME_PATTERN = re.compile(r"^(\d{1,2}):?(\d{2})(?::?(\d{2}))?")
MAX_TEXT = 120


class StatementError(ValueError):
    pass


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).replace("\u3000", " ").replace("\xa0", " ").strip()
    if text.startswith('="') and text.endswith('"'):
        text = text[2:-1]
    return text.strip().lstrip("'").strip()


def read_rows(path):
    """读出第一个工作表（或 CSV）的全部行，日期单元格保留为 datetime。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = max(book.worksheets, key=lambda item: item.max_row or 0)
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
        finally:
            book.close()
    elif suffix == ".xls":
        import xlrd

        try:
            book = xlrd.open_workbook(str(path))
        except xlrd.XLRDError as exc:
            raise StatementError("这个 .xls 文件打不开。可以先用 Excel 另存为 .xlsx 再导入。") from exc
        sheet = max(book.sheets(), key=lambda item: item.nrows)
        rows = []
        for index in range(sheet.nrows):
            row = []
            for cell in sheet.row(index):
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    row.append(cell.value)
            rows.append(row)
    elif suffix in (".csv", ".txt"):
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise StatementError("无法识别这个 CSV 文件的编码")
        sample = "\n".join(text.splitlines()[:40])
        delimiter = max(",\t;", key=sample.count)
        rows = [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    else:
        raise StatementError("只支持 .xlsx、.xls、.csv 文件")
    rows = [row for row in rows if any(_clean(cell) for cell in row)]
    if not rows:
        raise StatementError("文件里没有内容")
    return rows


def _norm(text):
    return re.sub(r"[\s:：（）()\[\]【】]", "", str(text)).lower()


def _alias_score(header, field):
    name = _norm(header)
    if not name:
        return 0
    best = 0
    for alias in ALIASES[field]:
        key = _norm(alias)
        if name == key:
            return 3
        if len(key) >= 2 and key in name:
            best = 2
    return best


def detect_header(rows):
    """表头通常是前 30 行里命中关键字最多的那一行。"""
    best, best_score = 0, -1
    for index, row in enumerate(rows[:30]):
        cells = [_clean(cell) for cell in row]
        score = sum(1 for cell in cells if cell and any(_alias_score(cell, field) for field in ALIASES))
        if score > best_score:
            best, best_score = index, score
    return best


def headers_at(rows, index):
    seen = {}
    out = []
    for position, cell in enumerate(rows[index] if index < len(rows) else []):
        name = _clean(cell) or f"第{position + 1}列"
        count = seen.get(name, 0)
        seen[name] = count + 1
        out.append(name if count == 0 else f"{name}({count + 1})")
    return out


def body(rows, header_row):
    return rows[header_row + 1:]


def column_values(rows, header_row, headers, name, limit=60):
    if name not in headers:
        return []
    position = headers.index(name)
    values = []
    for row in body(rows, header_row):
        if position < len(row) and _clean(row[position]):
            values.append(row[position])
            if len(values) >= limit:
                break
    return values


def parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _clean(value)
    if not text or text in ("-", "--", "—"):
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    for mark in (",", "，", "¥", "￥", "元", "RMB", "CNY", " "):
        text = text.replace(mark, "")
    if text.endswith("-"):
        negative, text = True, text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_date(value):
    """返回 (YYYY-MM-DD, HH:MM:SS 或空)；认不出返回 (None, "")。"""
    if isinstance(value, datetime):
        clock = value.strftime("%H:%M:%S") if value.time() != time(0, 0) else ""
        return value.date().isoformat(), clock
    if isinstance(value, date):
        return value.isoformat(), ""
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        moment = datetime(1899, 12, 30) + timedelta(days=float(value))
        return parse_date(moment)
    text = _clean(value)
    for pattern in DATE_PATTERNS:
        matched = pattern.match(text)
        if not matched:
            continue
        year, month, day_, hour, minute, second = matched.groups()
        try:
            found = date(int(year), int(month), int(day_))
        except ValueError:
            return None, ""
        clock = ""
        if hour is not None and minute is not None:
            clock = f"{int(hour):02d}:{int(minute):02d}:{int(second or 0):02d}"
        return found.isoformat(), clock
    return None, ""


def parse_time(value):
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = _clean(value)
    _day, clock = parse_date(text)
    if clock:
        return clock
    matched = TIME_PATTERN.match(text)
    if not matched:
        return ""
    hour, minute, second = matched.groups()
    if int(hour) > 23 or int(minute) > 59:
        return ""
    return f"{int(hour):02d}:{int(minute):02d}:{int(second or 0):02d}"


def direction_sign(value):
    text = _norm(_clean(value))
    if not text:
        return None
    if text in INCOME_WORDS or any(text.startswith(word) for word in ("收入", "存入", "转入", "贷")):
        return 1
    if text in EXPENSE_WORDS or any(text.startswith(word) for word in ("支出", "取出", "转出", "借")):
        return -1
    return None


def _kind_ok(field, values):
    if not values:
        return True
    if field == "txn_date":
        hits = sum(1 for value in values if parse_date(value)[0])
    elif field in ("amount", "income", "expense", "balance"):
        hits = sum(1 for value in values if parse_number(value) is not None)
    elif field == "direction":
        hits = sum(1 for value in values if direction_sign(value) is not None)
    elif field == "txn_time":
        hits = sum(1 for value in values if parse_time(value))
    else:
        return True
    return hits >= max(1, len(values) * 0.6)


def guess_map(rows, header_row=None):
    """按列名关键字和样本内容猜映射。"""
    if header_row is None:
        header_row = detect_header(rows)
    headers = headers_at(rows, header_row)
    used = set()
    columns = {field: None for field, _label in FIELDS}
    for field in GUESS_ORDER:
        ranked = sorted(
            ((_alias_score(name, field), -position, name) for position, name in enumerate(headers) if name not in used),
            reverse=True,
        )
        for score, _position, name in ranked:
            if score == 0:
                break
            if _kind_ok(field, column_values(rows, header_row, headers, name)):
                columns[field] = name
                used.add(name)
                break
    if columns["income"] and columns["expense"]:
        mode = "split"
    elif columns["amount"] and columns["direction"]:
        mode = "direction"
    else:
        mode = "signed"
    if mode == "signed" and not columns["amount"] and (columns["income"] or columns["expense"]):
        mode = "split"
    return {
        "version": 1,
        "header_row": header_row,
        "headers": headers,
        "columns": columns,
        "amount_mode": mode,
        "invert": False,
        "opening": None,
    }


def adapt(saved, rows):
    """沿用已保存的映射；列名变了就返回 (新映射, 新增列, 缺少列)，没变返回空列表。"""
    header_row = saved.get("header_row", 0)
    if header_row >= len(rows) or set(headers_at(rows, header_row)) != set(saved.get("headers") or []):
        found = detect_header(rows)
        if set(headers_at(rows, found)) == set(saved.get("headers") or []):
            header_row = found
    headers = headers_at(rows, header_row)
    old = set(saved.get("headers") or [])
    added = [name for name in headers if name not in old]
    missing = [name for name in saved.get("headers") or [] if name not in headers]
    mapping = {**saved, "header_row": header_row, "headers": headers}
    mapping["columns"] = {
        field: (name if name in headers else None) for field, name in (saved.get("columns") or {}).items()
    }
    for field, _label in FIELDS:
        mapping["columns"].setdefault(field, None)
    if added or missing:
        guessed = guess_map(rows, header_row)
        for field, name in guessed["columns"].items():
            if mapping["columns"].get(field) is None and name and name not in mapping["columns"].values():
                mapping["columns"][field] = name
    return mapping, added, missing


def required_missing(mapping):
    columns = mapping["columns"]
    lacking = [] if columns.get("txn_date") else ["交易日期"]
    for field in MODE_FIELDS[mapping["amount_mode"]]:
        if not columns.get(field):
            lacking.append(FIELD_LABEL[field])
    return lacking


def _consistency(rows):
    bad = flipped = checked = 0
    previous = None
    for row in rows:
        if row["balance"] is None:
            previous = None
            continue
        if previous is not None:
            checked += 1
            if abs(previous + row["amount"] - row["balance"]) > 0.011:
                bad += 1
            if abs(previous - row["amount"] - row["balance"]) > 0.011:
                flipped += 1
        previous = row["balance"]
    return bad, flipped, checked


def fingerprint(row, nth):
    balance = "" if row["balance"] is None else f"{row['balance']:.2f}"
    key = "|".join((row["txn_date"], row["txn_time"], f"{row['amount']:.2f}", balance, row["counterparty"], row["summary"], str(nth)))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def parse(rows, mapping):
    """按映射把表格解析成按时间排好序的交易。"""
    columns = mapping["columns"]
    headers = headers_at(rows, mapping["header_row"])
    where = {field: headers.index(name) for field, name in columns.items() if name and name in headers}
    mode = mapping["amount_mode"]
    sign = -1 if mapping.get("invert") else 1

    def cell(row, field):
        position = where.get(field)
        return None if position is None or position >= len(row) else row[position]

    parsed, skipped = [], []
    for offset, row in enumerate(body(rows, mapping["header_row"])):
        line = mapping["header_row"] + offset + 2
        day, clock = parse_date(cell(row, "txn_date"))
        if not day:
            text = _clean(cell(row, "txn_date"))
            skipped.append((line, f"日期认不出：{text[:20]}" if text else "没有日期"))
            continue
        if "txn_time" in where:
            clock = parse_time(cell(row, "txn_time")) or clock
        if mode == "split":
            income = parse_number(cell(row, "income"))
            expense = parse_number(cell(row, "expense"))
            if income is None and expense is None:
                skipped.append((line, "收入、支出都是空的"))
                continue
            amount = abs(income or 0) - abs(expense or 0)
        else:
            amount = parse_number(cell(row, "amount"))
            if amount is None:
                skipped.append((line, f"金额认不出：{_clean(cell(row, 'amount'))[:20]}"))
                continue
            if mode == "direction":
                direction = direction_sign(cell(row, "direction"))
                if direction is None:
                    skipped.append((line, f"收支方向认不出：{_clean(cell(row, 'direction'))[:20] or '空'}"))
                    continue
                amount = abs(amount) * direction
        balance = parse_number(cell(row, "balance")) if "balance" in where else None
        parsed.append({
            "line": line,
            "txn_date": day,
            "txn_time": clock,
            "amount": round(amount * sign, 2),
            "balance": None if balance is None else round(balance, 2),
            "counterparty": _clean(cell(row, "counterparty"))[:MAX_TEXT],
            "summary": _clean(cell(row, "summary"))[:MAX_TEXT],
            "note": _clean(cell(row, "note"))[:MAX_TEXT],
        })
    if len(parsed) > 1 and parsed[0]["txn_date"] > parsed[-1]["txn_date"]:
        parsed.reverse()
    ordered = sorted(parsed, key=lambda item: (item["txn_date"], item["txn_time"]))
    if parsed and parsed[0]["txn_date"] == parsed[-1]["txn_date"]:
        backward = sorted(reversed(parsed), key=lambda item: (item["txn_date"], item["txn_time"]))
        if _consistency(backward)[0] < _consistency(ordered)[0]:
            ordered = backward
    seen = {}
    for row in ordered:
        base = (row["txn_date"], row["txn_time"], row["amount"], row["balance"], row["counterparty"], row["summary"])
        seen[base] = seen.get(base, 0) + 1
        row["fingerprint"] = fingerprint(row, seen[base])
    bad, flipped, checked = _consistency(ordered)
    return {
        "rows": ordered,
        "skipped": skipped,
        "balance_checked": checked,
        "balance_bad": bad,
        "suggest_invert": checked > 0 and bad > checked / 2 and flipped < bad / 2,
    }


def daily_balances(txns, opening=None):
    """按日期得到每天日终余额。有余额列的行以余额为准，否则从期初余额累加金额。"""
    running = float(opening or 0)
    out = {}
    for row in txns:
        if row.get("balance") is not None:
            running = float(row["balance"])
        else:
            running = round(running + float(row["amount"]), 2)
        out[row["txn_date"]] = round(running, 2)
    return out


def snapshot_points(txns, opening=None):
    """每个月最后一个有交易的日期，加上最后一天，生成余额快照。"""
    daily = daily_balances(txns, opening)
    if not daily:
        return []
    picks = {}
    for day in sorted(daily):
        picks[day[:7]] = day
    days = sorted(set(picks.values()) | {max(daily)})
    return [(day, daily[day]) for day in days]
