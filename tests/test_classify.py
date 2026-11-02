import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from gxyfi import classify, db


def txn(day, amount, counterparty="", summary="", balance=None, nth=1):
    return {
        "txn_date": day, "txn_time": "", "amount": amount, "balance": balance,
        "counterparty": counterparty, "summary": summary, "note": "",
        "fingerprint": hashlib.sha1(f"{day}|{amount}|{counterparty}|{nth}".encode()).hexdigest()[:24],
    }


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GXYFI_DB"] = str(Path(self.tmp.name) / "t.db")
        db.init_db()
        account = {"institution": "", "kind": "活期", "currency": "CNY", "note": ""}
        self.card = db.save_account({**account, "name": "工资卡"})
        self.wallet = db.save_account({**account, "name": "支付宝"})
        self.cat = {row["name"]: row["id"] for row in db.list_categories()}

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("GXYFI_DB", None)

    def open_rows(self):
        return db.list_txns_all_open()

    def suggestions(self):
        everything = db._all("SELECT * FROM account_txns")
        rules = db.list_rules()
        rows = classify.pending([row for row in everything if not row["treat"]], rules)
        history = [row for row in everything if row["treat"] in ("支出", "退款")]
        return rows, classify.suggest(rows, rules, db.list_expenses(), db.list_categories(), history)

    def test_flow(self):
        db.import_txns(self.card, [
            txn("2026-10-01", -2000, "本人支付宝", "转账"),
            txn("2026-10-03", -35, "美团", "外卖"),
            txn("2026-10-05", -3000, "", "信用卡还款"),
            txn("2026-10-06", -88, "老王面馆", "消费"),
            txn("2026-10-08", -120, "某某公司", "消费"),
            txn("2026-10-09", 20, "美团", "退款"),
            txn("2026-10-10", 9000, "公司", "工资"),
        ], None, "a")
        db.import_txns(self.wallet, [txn("2026-10-02", 2000, "工资卡", "充值")], None, "b")
        db.save_expense({"category_id": self.cat["餐饮"], "occurred_on": "2026-10-06", "amount": 88, "payee": "面馆", "note": ""})

        from gxyfi.classify_ui import auto_transfers

        self.assertEqual(auto_transfers(), 1)
        treats = {row["counterparty"]: row["treat"] for row in db.list_txns(self.card) + db.list_txns(self.wallet)}
        self.assertEqual(treats["本人支付宝"], "转账")
        self.assertEqual(treats["工资卡"], "转账")

        rows, hints = self.suggestions()
        by_party = {(row["counterparty"] or row["summary"]): hints.get(row["id"]) for row in rows}
        self.assertNotIn("公司", by_party)
        self.assertEqual(by_party["美团"]["category_id"], self.cat["餐饮"])
        self.assertEqual(by_party["信用卡还款"]["treat"], "不计")
        self.assertTrue(by_party["老王面馆"]["merge"])
        self.assertIsNone(by_party["某某公司"])
        refund = next(row for row in rows if row["amount"] > 0)
        self.assertEqual(hints[refund["id"]]["treat"], "退款")

        items = [{"id": row["id"], **{key: hints[row["id"]][key] for key in ("treat", "category_id", "expense_id")}} for row in rows if row["id"] in hints]
        other = next(row for row in rows if row["counterparty"] == "某某公司")
        items.append({"id": other["id"], "treat": "支出", "category_id": self.cat["日用"]})
        db.classify_txns(items, [classify.rule_for(other, "支出", self.cat["日用"])])

        expenses = db.list_expenses()
        self.assertEqual(len(expenses), 4)
        merged = next(row for row in expenses if row["payee"] == "面馆")
        self.assertIsNotNone(merged["txn_id"])
        self.assertEqual(merged["source"], "手工")
        self.assertEqual(sorted(round(row["amount"], 2) for row in expenses), [-20, 35, 88, 120])
        food = next(row for row in db.budget_progress(2026) if row["category"] == "餐饮")
        self.assertEqual(food["actual"], 103)
        self.assertEqual(db.pending_count(), 0)

        db.import_txns(self.card, [txn("2026-10-20", -60, "某某公司", "消费")], None, "c")
        rows, hints = self.suggestions()
        self.assertEqual(hints[rows[0]["id"]]["category_id"], self.cat["日用"])
        self.assertIn("规则", hints[rows[0]["id"]]["reason"])
        db.delete_rules([rule["id"] for rule in db.list_rules()])
        self.assertEqual(db.list_rules(), [])
        rows, hints = self.suggestions()
        self.assertNotIn("规则", hints.get(rows[0]["id"], {}).get("reason", ""))

        meituan = next(row for row in db.list_txns(self.card) if row["counterparty"] == "美团" and row["amount"] < 0)
        db.save_txn({**meituan, "amount": -40}, meituan["id"])
        self.assertIn(40, [row["amount"] for row in db.list_expenses()])
        db.delete_txns([meituan["id"]])
        self.assertNotIn(40, [row["amount"] for row in db.list_expenses()])

        noodle = next(row for row in db.list_txns(self.card) if row["counterparty"] == "老王面馆")
        db.delete_txns([noodle["id"]])
        kept = next(row for row in db.list_expenses() if row["payee"] == "面馆")
        self.assertIsNone(kept["txn_id"])

        other_expense = next(row for row in db.list_expenses() if row["amount"] == 120)
        db.delete_expense(other_expense["id"])
        self.assertEqual(next(row for row in db.list_txns(self.card) if row["amount"] == -120)["treat"], "不计")

        db.delete_txn_batch(self.card, "a")
        self.assertEqual([row["payee"] for row in db.list_expenses() if row["txn_id"]], [])
        self.assertIn("面馆", [row["payee"] for row in db.list_expenses()])

    def test_refund_requires_income(self):
        db.import_txns(self.card, [txn("2026-10-03", -35, "美团")], None, "a")
        row = db.list_txns(self.card)[0]
        with self.assertRaises(ValueError):
            db.classify_txns([{"id": row["id"], "treat": "退款", "category_id": self.cat["餐饮"]}])
        with self.assertRaises(ValueError):
            db.classify_txns([{"id": row["id"], "treat": "支出"}])


if __name__ == "__main__":
    unittest.main()
