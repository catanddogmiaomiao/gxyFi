"""基金定投的份额与成本。历史可以记一笔期初，也可以按确认记录逐笔导入。"""

from __future__ import annotations

from datetime import date, datetime

CYCLES = ("每月", "每周", "每两周")
STATUSES = ("进行中", "已暂停", "已结束")
KINDS = ("期初", "定投", "赎回", "红利再投")
WEEKDAYS = ("", "周一", "周二", "周三", "周四", "周五")
TEMPLATE_HEADERS = ["确认日期", "类型", "扣款金额", "确认净值", "确认份额", "手续费", "备注"]

KIND_ALIASES = {
    "期初": "期初",
    "持仓": "期初",
    "定投": "定投",
    "买入": "定投",
    "申购": "定投",
    "赎回": "赎回",
    "卖出": "赎回",
    "红利再投": "红利再投",
    "分红再投": "红利再投",
}
HEADER_MAP = {
    "确认日期": "trade_date",
    "日期": "trade_date",
    "类型": "kind",
    "扣款金额": "amount",
    "申请金额": "amount",
    "金额": "amount",
    "到账金额": "amount",
    "成本金额": "amount",
    "成本": "amount",
    "确认净值": "nav",
    "净值": "nav",
    "确认份额": "shares",
    "份额": "shares",
    "手续费": "fee",
    "备注": "note",
}


def cycle_label(cycle, debit_day):
    day = int(debit_day)
    if cycle == "每月":
        return f"每月 {day} 日"
    if 1 <= day <= 5:
        return f"{cycle} {WEEKDAYS[day]}"
    return f"{cycle} {day}"


def normalize_symbol(raw):
    text = str(raw or "").strip()
    if not text.isdigit() or len(text) > 6:
        raise ValueError("基金代码是 6 位数字")
    return text.zfill(6)


def _number(value, label, required=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"请填写{label}")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}不是有效数字")
    try:
        number = float(str(value).strip().replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        raise ValueError(f"{label}不是有效数字") from None
    return number


def _date_text(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip().replace("/", "-").replace(".", "-")
    if not text:
        raise ValueError("请填写确认日期")
    text = text[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError("确认日期请写成 YYYY-MM-DD") from None
    return text


def normalize_record(data):
    kind = KIND_ALIASES.get(str(data.get("kind") or "定投").strip(), "")
    if kind not in KINDS:
        raise ValueError("类型只能是期初、定投、赎回或红利再投")
    trade_date = _date_text(data.get("trade_date"))
    amount = _number(data.get("amount"), "金额") or 0.0
    fee = _number(data.get("fee"), "手续费") or 0.0
    nav = _number(data.get("nav"), "确认净值")
    shares = _number(data.get("shares"), "确认份额")
    if amount < 0 or fee < 0:
        raise ValueError("金额和手续费不能为负数")
    if nav is not None and nav <= 0:
        raise ValueError("确认净值需要大于 0")
    if kind == "定投":
        if amount <= 0:
            raise ValueError("定投需要填写扣款金额")
        if shares is None:
            if nav is None:
                raise ValueError("请填写确认份额，或同时填写确认净值")
            net = amount - fee
            if net <= 0:
                raise ValueError("扣款金额需要大于手续费")
            shares = net / nav
    elif shares is None or shares <= 0:
        raise ValueError("份额需要大于 0")
    if shares <= 0:
        raise ValueError("份额需要大于 0")
    return {
        "kind": kind,
        "trade_date": trade_date,
        "amount": round(amount, 2),
        "nav": None if nav is None else round(nav, 4),
        "shares": round(shares, 4),
        "fee": round(fee, 2),
        "note": str(data.get("note") or "").strip(),
    }


def apply_position(records):
    ordered = sorted(records, key=lambda row: (row["trade_date"], row.get("id") or 0))
    shares = 0.0
    cost = 0.0
    for row in ordered:
        kind = row["kind"]
        qty = float(row["shares"] or 0)
        if kind in ("期初", "定投"):
            shares += qty
            cost += float(row["amount"] or 0)
        elif kind == "赎回":
            if qty - shares > 0.0001:
                raise ValueError(f"{row['trade_date']} 的赎回份额超过当时持有份额")
            if shares <= 0:
                raise ValueError(f"{row['trade_date']} 赎回时还没有持有份额")
            qty = min(qty, shares)
            cost -= cost * (qty / shares)
            shares -= qty
        elif kind == "红利再投":
            shares += qty
        if shares <= 0.0001:
            shares = 0.0
            cost = 0.0
        elif cost < 0:
            cost = 0.0
    return round(shares, 4), round(cost, 2)


def latest_nav(manual_nav, records):
    if manual_nav is not None:
        return manual_nav, "手工"
    dated = [row for row in records if row.get("nav") is not None]
    if not dated:
        return None, ""
    last = max(dated, key=lambda row: (row["trade_date"], row.get("id") or 0))
    return last["nav"], "最近确认"


def parse_rows(rows):
    if not rows:
        raise ValueError("表格是空的")
    header = []
    for cell in rows[0]:
        text = "" if cell is None else str(cell).strip()
        header.append(HEADER_MAP.get(text))
    if "trade_date" not in header:
        raise ValueError("表头需要有「确认日期」或「日期」")
    if "shares" not in header and "nav" not in header:
        raise ValueError("表头需要有「确认份额」或「确认净值」")
    parsed = []
    errors = []
    for index, row in enumerate(rows[1:], start=2):
        if row is None or all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        item = {}
        for key, cell in zip(header, row):
            if key and key not in item:
                item[key] = cell
        if not str(item.get("kind") or "").strip():
            item["kind"] = "定投"
        try:
            parsed.append(normalize_record(item))
        except ValueError as exc:
            errors.append(f"第 {index} 行：{exc}")
    if errors:
        raise ValueError("\n".join(errors[:8]))
    if not parsed:
        raise ValueError("没有可导入的记录")
    return parsed


def read_xlsx(path):
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    return parse_rows(rows)


def write_template(path):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "定投记录"
    sheet.append(TEMPLATE_HEADERS)
    note = workbook.create_sheet("说明")
    note["A1"] = (
        "类型填期初、定投、赎回或红利再投。定投的确认份额可以留空，按（扣款金额−手续费）/确认净值计算。"
        "只知道当前份额时，记一条期初即可，不要再把同一段份额按明细导入一遍。"
    )
    note.column_dimensions["A"].width = 88
    workbook.save(path)
