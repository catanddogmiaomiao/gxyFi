"""给导入的明细找归类建议：自己账户间的转账、可能重复的手工支出、学到的规则、常见商户。"""

from __future__ import annotations

from datetime import date

REFUND_WORDS = ("退款", "退货", "退回", "冲正", "返还", "撤销")
EXCLUDE_WORDS = (
    "信用卡还款", "还款", "理财", "申购", "认购", "基金", "定期", "证券", "银证", "转存",
    "余额宝", "零钱通", "提现", "充值", "本人", "自己",
)
CATEGORY_HINTS = {
    "餐饮": ("美团", "饿了么", "餐", "饭", "咖啡", "星巴克", "瑞幸", "肯德基", "麦当劳", "外卖", "奶茶", "食堂", "面馆"),
    "交通": ("滴滴", "地铁", "公交", "加油", "中石化", "中石油", "停车", "高速", "ETC", "12306", "铁路", "航空", "机票", "打车", "出行"),
    "居住": ("物业", "水费", "电费", "燃气", "房租", "租金", "宽带", "自来水", "供电"),
    "医疗": ("医院", "药房", "药店", "大药", "诊所", "体检", "卫生"),
    "保险": ("保险", "人寿", "财险"),
    "日用": ("超市", "京东", "淘宝", "天猫", "拼多多", "便利店", "盒马", "永辉", "沃尔玛", "山姆"),
    "教育": ("学费", "培训", "书店", "课程", "学校", "教育"),
    "娱乐": ("电影", "影城", "游戏", "旅游", "酒店", "门票", "会员"),
    "人情": ("红包", "礼金", "份子"),
}
TRANSFER_DAYS = 3
MERGE_DAYS = 2


def _text(txn):
    return f"{txn.get('counterparty') or ''} {txn.get('summary') or ''} {txn.get('note') or ''}"


def _days(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def is_refund_like(txn, rules=()):
    if txn["amount"] <= 0:
        return False
    if any(word in _text(txn) for word in REFUND_WORDS):
        return True
    rule = match_rule(txn, rules)
    return rule is not None and rule["treat"] == "退款"


def pending(txns, rules=()):
    """需要处理的：所有没归类的支出，加上像退款的收入。"""
    return [row for row in txns if not row.get("treat") and (row["amount"] < 0 or is_refund_like(row, rules))]


def find_transfers(txns):
    """在不同账户之间找金额相同、方向相反、日期相近的两笔，视为自己账户间的转账。"""
    incoming = {}
    for row in txns:
        if not row.get("treat") and row["amount"] > 0:
            incoming.setdefault(round(row["amount"], 2), []).append(row)
    used = set()
    pairs = []
    for out in sorted((row for row in txns if not row.get("treat") and row["amount"] < 0), key=lambda item: item["txn_date"]):
        candidates = [
            row for row in incoming.get(round(-out["amount"], 2), [])
            if row["id"] not in used and row["account_id"] != out["account_id"] and _days(row["txn_date"], out["txn_date"]) <= TRANSFER_DAYS
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda row: (_days(row["txn_date"], out["txn_date"]), row["id"]))
        used.add(best["id"])
        pairs.append((out["id"], best["id"]))
    return pairs


def match_rule(txn, rules):
    best = None
    for rule in rules:
        value = (txn.get(rule["field"]) or "").strip()
        keyword = rule["keyword"]
        if not value or not keyword:
            continue
        if value == keyword:
            score = (2, len(keyword))
        elif keyword in value:
            score = (1, len(keyword))
        else:
            continue
        if best is None or score > best[0]:
            best = (score, rule)
    return None if best is None else best[1]


def rule_for(txn, treat, category_id):
    """用户归类一笔后，记住「对方户名」，没有对方户名时记住摘要。"""
    if treat not in ("支出", "退款", "不计"):
        return None
    counterparty = (txn.get("counterparty") or "").strip()
    summary = (txn.get("summary") or "").strip()
    if counterparty:
        return {"field": "counterparty", "keyword": counterparty[:40], "treat": treat, "category_id": category_id}
    if summary:
        return {"field": "summary", "keyword": summary[:40], "treat": treat, "category_id": category_id}
    return None


def suggest(rows, rules, manual_expenses, categories, history):
    """返回 {明细 id: 建议}。建议含 treat、category_id、expense_id、reason、merge。"""
    by_name = {row["name"]: row["id"] for row in categories}
    names = {row["id"]: row["name"] for row in categories}
    learned = {}
    for row in sorted(history, key=lambda item: item["txn_date"]):
        key = (row.get("counterparty") or "").strip()
        if key and row.get("category_id") and row.get("treat") in ("支出", "退款"):
            learned[key] = row["category_id"]
    open_manual = [row for row in manual_expenses if not row.get("txn_id") and (row.get("source") or "手工") == "手工"]
    used = set()
    out = {}
    for txn in sorted(rows, key=lambda item: (item["txn_date"], item["id"])):
        if txn["amount"] < 0:
            amount = round(-txn["amount"], 2)
            candidates = [
                row for row in open_manual
                if row["id"] not in used and abs(row["amount"] - amount) < 0.005 and _days(row["occurred_on"], txn["txn_date"]) <= MERGE_DAYS
            ]
            if candidates:
                best = min(candidates, key=lambda row: (_days(row["occurred_on"], txn["txn_date"]), row["id"]))
                used.add(best["id"])
                out[txn["id"]] = {
                    "treat": "支出", "category_id": best["category_id"], "expense_id": best["id"], "merge": True,
                    "reason": f"可能和手工记录重复：{best['occurred_on']} {names.get(best['category_id'], '')} {best['amount']:,.2f} {best.get('payee') or ''}".strip(),
                }
                continue
        rule = match_rule(txn, rules)
        if rule is not None:
            treat = rule["treat"]
            if (treat == "支出" and txn["amount"] < 0) or (treat == "退款" and txn["amount"] > 0) or treat == "不计":
                out[txn["id"]] = {
                    "treat": treat, "category_id": rule.get("category_id"), "expense_id": None, "merge": False,
                    "reason": f"按规则：{'对方' if rule['field'] == 'counterparty' else '摘要'}「{rule['keyword']}」",
                }
                continue
        counterparty = (txn.get("counterparty") or "").strip()
        treat = "退款" if txn["amount"] > 0 else "支出"
        if counterparty in learned and learned[counterparty] in names:
            out[txn["id"]] = {
                "treat": treat, "category_id": learned[counterparty], "expense_id": None, "merge": False,
                "reason": f"「{counterparty}」以前归为这一类",
            }
            continue
        text = _text(txn)
        if txn["amount"] < 0:
            hit = next((word for word in EXCLUDE_WORDS if word in text), None)
            if hit:
                out[txn["id"]] = {"treat": "不计", "category_id": None, "expense_id": None, "merge": False, "reason": f"像是还款、理财或转存（含「{hit}」）"}
                continue
        for name, words in CATEGORY_HINTS.items():
            hit = next((word for word in words if word in text), None)
            if hit and name in by_name:
                out[txn["id"]] = {
                    "treat": treat, "category_id": by_name[name], "expense_id": None, "merge": False,
                    "reason": f"含「{hit}」",
                }
                break
    return out


def describe(treat, category_id, names):
    if treat == "支出":
        return names.get(category_id, "支出")
    if treat == "退款":
        return f"退款 · 冲减{names.get(category_id, '')}"
    if treat == "不计":
        return "不算支出"
    if treat == "转账":
        return "自己账户间转账"
    return ""
