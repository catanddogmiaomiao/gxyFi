import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from gxyfi import db, statement


def bank_xlsx(path, rows, headers=("交易日期", "交易时间", "收入金额", "支出金额", "账户余额", "对方户名", "摘要")):
    wb = Workbook()
    ws = wb.active
    ws.append(["招商银行交易流水"])
    ws.append(["账号：6214********1234", None, "币种：人民币"])
    ws.append([])
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    ws.append(["合计", None, 1000, 300])
    wb.save(path)


BANK = [
    ("2026-10-08", "09:00:00", None, 100, 1600, "超市", "消费"),
    ("2026-09-30", "18:00:00", None, 200, 1700, "房东", "转账"),
    ("2026-09-15", "10:00:00", 1000, None, 1900, "公司", "工资"),
]


class StatementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        os.environ["GXYFI_DB"] = str(self.dir / "t.db")
        db.init_db()
        self.account = db.save_account({"name": "招行", "institution": "", "kind": "活期", "currency": "CNY", "note": ""})

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("GXYFI_DB", None)

    def test_bank_file_guess_and_balances(self):
        path = self.dir / "bank.xlsx"
        bank_xlsx(path, BANK)
        rows = statement.read_rows(path)
        mapping = statement.guess_map(rows)
        self.assertEqual(mapping["header_row"], 2)
        self.assertEqual(mapping["amount_mode"], "split")
        self.assertEqual(mapping["columns"]["txn_date"], "交易日期")
        self.assertEqual(mapping["columns"]["txn_time"], "交易时间")
        self.assertEqual(mapping["columns"]["balance"], "账户余额")
        self.assertEqual(mapping["columns"]["counterparty"], "对方户名")
        self.assertEqual(mapping["columns"]["summary"], "摘要")
        result = statement.parse(rows, mapping)
        self.assertEqual([row["txn_date"] for row in result["rows"]], ["2026-09-15", "2026-09-30", "2026-10-08"])
        self.assertEqual([row["amount"] for row in result["rows"]], [1000, -200, -100])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["balance_bad"], 0)

        added, skipped = db.import_txns(self.account, result["rows"], mapping, "第一次")
        self.assertEqual((added, skipped), (3, 0))
        balances = {row["as_of_date"]: (row["balance"], row["source"]) for row in db.list_balances(self.account)}
        self.assertEqual(balances, {"2026-09-30": (1700, "明细"), "2026-10-08": (1600, "明细")})

        bank_xlsx(path, [("2026-10-20", "12:00:00", None, 50, 1550, "餐馆", "消费")] + BANK)
        again = statement.parse(statement.read_rows(path), db.get_import_map(self.account))
        self.assertEqual(db.import_txns(self.account, again["rows"], None, "第二次"), (1, 3))
        self.assertEqual(db.latest_balance(self.account)["balance"], 1550)

        txn = next(row for row in db.list_txns(self.account) if row["txn_date"] == "2026-10-20")
        db.save_txn({**txn, "balance": 1500}, txn["id"])
        self.assertEqual(db.latest_balance(self.account)["balance"], 1500)
        db.delete_txn_batch(self.account, "第二次")
        self.assertEqual(db.latest_balance(self.account)["balance"], 1600)
        db.save_balance({"account_id": self.account, "as_of_date": "2026-11-01", "balance": 1234, "note": ""})
        db.delete_txns([row["id"] for row in db.list_txns(self.account)])
        self.assertEqual([row["source"] for row in db.list_balances(self.account)], ["手工"])

    def test_changed_columns_detected(self):
        path = self.dir / "bank.xlsx"
        bank_xlsx(path, BANK)
        mapping = statement.guess_map(statement.read_rows(path))
        headers = ("交易日期", "交易时间", "收入金额", "支出金额", "账户余额", "交易对方", "摘要", "渠道")
        bank_xlsx(path, [row + ("手机银行",) for row in BANK], headers)
        adapted, added, missing = statement.adapt(mapping, statement.read_rows(path))
        self.assertEqual(added, ["交易对方", "渠道"])
        self.assertEqual(missing, ["对方户名"])
        self.assertEqual(adapted["columns"]["counterparty"], "交易对方")
        self.assertEqual(adapted["columns"]["balance"], "账户余额")
        same, added, missing = statement.adapt(mapping, statement.read_rows(self._bank_copy()))
        self.assertEqual((added, missing), ([], []))
        self.assertEqual(same["columns"], mapping["columns"])

    def _bank_copy(self):
        path = self.dir / "copy.xlsx"
        bank_xlsx(path, BANK)
        return path

    def test_alipay_csv_direction(self):
        path = self.dir / "alipay.csv"
        lines = [
            "支付宝交易明细",
            "交易时间,交易分类,交易对方,商品说明,收/支,金额,交易状态",
            "2026-10-02 12:30:00,餐饮,面馆,牛肉面,支出,35.00,交易成功",
            "2026-10-01 09:00:00,转账,张三,还款,收入,500.00,交易成功",
            "2026-10-01 08:00:00,理财,余额宝,转入,不计收支,100.00,交易成功",
        ]
        path.write_bytes("\n".join(lines).encode("gb18030"))
        rows = statement.read_rows(path)
        mapping = statement.guess_map(rows)
        self.assertEqual(mapping["header_row"], 1)
        self.assertEqual(mapping["amount_mode"], "direction")
        self.assertEqual(mapping["columns"]["txn_date"], "交易时间")
        self.assertEqual(mapping["columns"]["counterparty"], "交易对方")
        result = statement.parse(rows, mapping)
        self.assertEqual([(row["txn_date"], row["txn_time"], row["amount"]) for row in result["rows"]], [
            ("2026-10-01", "09:00:00", 500.0), ("2026-10-02", "12:30:00", -35.0),
        ])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("不计收支", result["skipped"][0][1])

        mapping["opening"] = 1000
        db.import_txns(self.account, result["rows"], mapping, "支付宝")
        self.assertEqual(db.latest_balance(self.account)["balance"], 1465)

    def test_manual_balance_anchors_transactions_without_balance(self):
        rows = [
            {"txn_date": "2026-10-28", "txn_time": "", "amount": -10, "balance": None,
             "counterparty": "", "summary": "", "note": "", "fingerprint": "a" * 24},
            {"txn_date": "2026-10-31", "txn_time": "", "amount": -5, "balance": None,
             "counterparty": "", "summary": "", "note": "", "fingerprint": "b" * 24},
            {"txn_date": "2026-11-01", "txn_time": "", "amount": -2, "balance": None,
             "counterparty": "", "summary": "", "note": "", "fingerprint": "c" * 24},
        ]
        db.import_txns(self.account, rows, {"opening": 0}, "卡")
        self.assertEqual(db.latest_balance(self.account)["balance"], -17)
        entry = db.save_balance({"account_id": self.account, "as_of_date": "2026-11-02", "balance": -1000, "note": "账单"})
        self.assertEqual(db.latest_balance(self.account)["as_of_date"], "2026-11-02")
        db.save_balance({"account_id": self.account, "as_of_date": "2026-10-27", "balance": -1000, "note": "账单"}, entry)
        self.assertEqual(
            [(row["as_of_date"], row["balance"], row["source"]) for row in reversed(db.list_balances(self.account))],
            [("2026-10-27", -1000, "手工"), ("2026-10-31", -1015, "明细"), ("2026-11-01", -1017, "明细")],
        )
        db.delete_balance(entry)
        self.assertEqual(db.latest_balance(self.account)["balance"], -17)

    def test_debit_credit_flag_and_invert_hint(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["记账日期", "发生额", "借贷标志", "余额", "用途"])
        ws.append([datetime(2026, 8, 1), "1,000.00", "贷", "5,000.00", "工资"])
        ws.append([datetime(2026, 8, 3), "200.00", "借", "4,800.00", "房租"])
        path = self.dir / "flag.xlsx"
        wb.save(path)
        rows = statement.read_rows(path)
        mapping = statement.guess_map(rows)
        self.assertEqual(mapping["amount_mode"], "direction")
        self.assertEqual(mapping["columns"]["direction"], "借贷标志")
        result = statement.parse(rows, mapping)
        self.assertEqual([row["amount"] for row in result["rows"]], [1000, -200])
        self.assertEqual(result["balance_bad"], 0)

        wb = Workbook()
        ws = wb.active
        ws.append(["交易日期", "交易金额", "余额"])
        ws.append(["2026-08-01", "100", "900"])
        ws.append(["2026-08-02", "50", "850"])
        ws.append(["2026-08-03", "-20", "870"])
        path = self.dir / "flip.xlsx"
        wb.save(path)
        rows = statement.read_rows(path)
        mapping = statement.guess_map(rows)
        result = statement.parse(rows, mapping)
        self.assertTrue(result["suggest_invert"])
        mapping["invert"] = True
        self.assertEqual(statement.parse(rows, mapping)["balance_bad"], 0)

    def test_duplicate_rows_kept_apart(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["日期", "金额"])
        ws.append(["2026-08-01", "-10"])
        ws.append(["2026-08-01", "-10"])
        path = self.dir / "dup.xlsx"
        wb.save(path)
        result = statement.parse(statement.read_rows(path), statement.guess_map(statement.read_rows(path)))
        self.assertEqual(len({row["fingerprint"] for row in result["rows"]}), 2)
        self.assertEqual(db.import_txns(self.account, result["rows"], None, "a"), (2, 0))
        self.assertEqual(db.import_txns(self.account, result["rows"], None, "b"), (0, 2))

    def test_dates(self):
        self.assertEqual(statement.parse_date("2026年3月5日"), ("2026-03-05", ""))
        self.assertEqual(statement.parse_date("20260305 141500"), ("2026-03-05", "14:15:00"))
        self.assertEqual(statement.parse_date("2026/3/5 9:05"), ("2026-03-05", "09:05:00"))
        self.assertEqual(statement.parse_date(46000.5)[0], "2025-12-09")
        self.assertEqual(statement.parse_number("(1,234.50)"), -1234.5)
        self.assertEqual(statement.parse_number("¥35.00"), 35.0)
        self.assertEqual(statement.parse_number("12.00-"), -12.0)


if __name__ == "__main__":
    unittest.main()
