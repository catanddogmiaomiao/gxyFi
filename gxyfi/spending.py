"""支出按类别、按月份汇总，供支出页和总览的图表使用。退款是负数，冲减所在类别。"""

from __future__ import annotations

from datetime import date

OTHER = "其他类别"


def in_period(row, period):
    return not period or row["occurred_on"].startswith(period)


def previous_period(period):
    """「2026-11」的上一期是「2026-10」，「2026」的上一期是「2025」，全部月份没有上一期。"""
    if not period:
        return None
    if len(period) == 4:
        return f"{int(period) - 1:04d}"
    year, month = int(period[:4]), int(period[5:7])
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"


def period_label(period):
    if not period:
        return "全部月份"
    if len(period) == 4:
        return f"{period} 年"
    return f"{int(period[:4])} 年 {int(period[5:7])} 月"


def by_category(expenses, period=None):
    """返回按金额从大到小的类别汇总：category_id、category、amount、count、refund、share。"""
    groups = {}
    for row in expenses:
        if not in_period(row, period):
            continue
        item = groups.setdefault(row["category_id"], {
            "category_id": row["category_id"], "category": row["category"], "amount": 0.0, "count": 0, "refund": 0.0,
        })
        item["amount"] += row["amount"]
        item["count"] += 1
        if row["amount"] < 0:
            item["refund"] += -row["amount"]
    rows = [{**item, "amount": round(item["amount"], 2), "refund": round(item["refund"], 2)} for item in groups.values()]
    total = sum(max(item["amount"], 0) for item in rows)
    for item in rows:
        item["share"] = max(item["amount"], 0) / total if total else 0.0
    rows.sort(key=lambda item: (-item["amount"], item["category"]))
    return rows


def total(expenses, period=None):
    return round(sum(row["amount"] for row in expenses if in_period(row, period)), 2)


def month_keys(count, today=None):
    today = today or date.today()
    year, month = today.year, today.month
    keys = []
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(keys))


def monthly_stack(expenses, count=6, top=5, today=None):
    """近 count 个月每月各类别金额。只单独列出合计最多的 top 类，其余并成「其他类别」。

    返回 (月份列表, 类别名列表, {月份: {类别名: 金额}})。
    """
    keys = month_keys(count, today)
    wanted = set(keys)
    cells = {key: {} for key in keys}
    totals = {}
    for row in expenses:
        key = row["occurred_on"][:7]
        if key not in wanted:
            continue
        cells[key][row["category"]] = cells[key].get(row["category"], 0.0) + row["amount"]
        totals[row["category"]] = totals.get(row["category"], 0.0) + row["amount"]
    ranked = [name for name, value in sorted(totals.items(), key=lambda item: -item[1]) if value > 0]
    kept = ranked[:top]
    merged = len(ranked) > top
    out = {}
    for key in keys:
        month = {}
        for name, value in cells[key].items():
            target = name if name in kept else OTHER
            month[target] = month.get(target, 0.0) + value
        out[key] = {name: round(max(value, 0), 2) for name, value in month.items() if value > 0}
    names = kept + ([OTHER] if merged else [])
    return keys, names, out


def budget_for(period, budgets):
    """本期对应的预算：按月看用年度预算的十二分之一，按年看用全年，全部月份不比较。

    budgets: budget_progress() 的结果。返回 {category_id: 金额}。
    """
    if not period:
        return {}
    share = 1 if len(period) == 4 else 1 / 12
    return {row["category_id"]: round(row["planned"] * share, 2) for row in budgets if row["planned"]}
