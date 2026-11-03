import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook

from gxyfi import db, dca, market
from gxyfi.export_excel import export_workbook


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GXYFI_DB"] = str(Path(self.tmp.name) / "t.db")
        db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("GXYFI_DB", None)

    def test_symbols(self):
        self.assertEqual(market.to_sina_symbol("600519"), "sh600519")
        self.assertEqual(market.to_sina_symbol("sh600519"), "sh600519")
        self.assertEqual(market.to_sina_symbol("000001"), "sz000001")
        self.assertEqual(market.to_sina_symbol("300750"), "sz300750")
        self.assertEqual(market.to_sina_symbol("513100"), "sh513100")
        self.assertEqual(market.to_sina_symbol("159915"), "sz159915")
        self.assertEqual(market.to_sina_symbol("830799"), "bj830799")
        self.assertEqual(market.to_yahoo_symbol("brk.b"), "BRK-B")
        self.assertEqual(market.canonical_gold("au(t+d)"), "Au(T+D)")
        prepared = market.prepare_holding({"market": "US", "symbol": "aapl", "name": "", "currency": "CNY"})
        self.assertEqual(prepared["symbol"], "AAPL")
        self.assertEqual(prepared["currency"], "USD")
        self.assertEqual(prepared["name"], "AAPL")

    def test_parsers(self):
        sge = market.parse_sge_payload(
            {
                "times": ["15:29", "15:30", "15:31"],
                "data": ["854.72", "854.08", "999"],
                "delaystr": "2026年10月30日 15:31:00",
                "heyue": "Au(T+D)",
            }
        )
        self.assertEqual(sge["price"], 854.08)
        sina = market.parse_sina_text(
            'var hq_str_sh600519="贵州茅台,1,1354,1392.05,' + ",".join(["0"] * 26) + ',2026-10-30,15:00:00,00";'
        )
        self.assertEqual(sina["name"], "贵州茅台")
        self.assertEqual(sina["price"], 1392.05)
        self.assertEqual(sina["quoted_at"], "2026-10-30 15:00:00")
        east = market.parse_eastmoney_payload({"data": {"f43": 139205, "f58": "贵州茅台", "f60": 135400, "f59": 2}})
        self.assertAlmostEqual(east["price"], 1392.05)
        yahoo = market.parse_yahoo_chart(
            {"chart": {"result": [{"meta": {"regularMarketPrice": 10.5, "chartPreviousClose": 10, "currency": "USD", "shortName": "Apple", "regularMarketTime": 0}}]}}
        )
        self.assertEqual(yahoo["price"], 10.5)
        self.assertEqual(yahoo["currency"], "USD")
        # chartPreviousClose 故意给错，日 K 倒数第二根才是昨收
        yahoo = market.parse_yahoo_chart({
            "chart": {"result": [{
                "meta": {"regularMarketPrice": 59.97, "chartPreviousClose": 90.47, "currency": "USD", "shortName": "Intel", "regularMarketTime": 0},
                "indicators": {"quote": [{"close": [92.07, 65.76, 59.97]}]},
            }]},
        })
        self.assertAlmostEqual(yahoo["prev_close"], 65.76)

    def test_ledger_math(self):
        account = db.save_account({"name": "工资卡", "institution": "招行", "kind": "活期", "currency": "CNY", "note": ""})
        usd = db.save_account({"name": "美元", "institution": "", "kind": "现金", "currency": "USD", "note": ""})
        db.save_balance({"account_id": account, "as_of_date": "2026-09-01", "balance": 100, "note": ""})
        db.save_balance({"account_id": account, "as_of_date": "2026-10-01", "balance": 300, "note": "月末"})
        db.save_balance({"account_id": usd, "as_of_date": "2026-10-01", "balance": 10, "note": ""})
        latest = {row["id"]: row["balance"] for row in db.latest_account_rows()}
        self.assertEqual(latest[account], 300)
        summary = db.portfolio_summary(date(2026, 10, 30))
        self.assertEqual(summary["savings_cny"], 300)
        self.assertTrue(summary["savings_missing"])
        db.save_fx_rates({"USD": {"rate": 7, "source": "手工", "at": "2026-10-30"}})
        summary = db.portfolio_summary(date(2026, 10, 30))
        self.assertEqual(summary["savings_cny"], 370)
        self.assertEqual(summary["stale_accounts"], [])
        old = db.portfolio_summary(date(2026, 12, 20))
        self.assertEqual(len(old["stale_accounts"]), 2)

        holding = db.save_holding(
            market.prepare_holding(
                {
                    "name": "黄金延期",
                    "symbol": "Au(T+D)",
                    "market": "GOLD",
                    "quantity": 10,
                    "avg_cost": 800,
                    "manual_price": 850,
                    "currency": "USD",
                    "account_label": "",
                    "note": "",
                }
            )
        )
        rows, total, missing = db.valued_holdings()
        self.assertEqual(holding > 0, True)
        self.assertEqual(rows[0]["currency"], "CNY")
        self.assertEqual(rows[0]["price"], 850)
        self.assertEqual(rows[0]["source"], "手工")
        self.assertAlmostEqual(total, 8500)
        self.assertFalse(missing)
        db.save_quotes(
            [
                {
                    "market": "GOLD",
                    "symbol": "Au(T+D)",
                    "name": "Au(T+D)",
                    "price": 900,
                    "prev_close": 880,
                    "currency": "CNY",
                    "quoted_at": "2026-10-30",
                    "source": "上海黄金交易所（延时）",
                    "ok": True,
                    "message": "",
                }
            ]
        )
        rows, _total, _missing = db.valued_holdings()
        self.assertEqual(rows[0]["price"], 850)

        db.save_property(
            {
                "name": "住宅",
                "kind": "住宅",
                "city": "上海",
                "address": "",
                "area_sqm": 80,
                "acquired_on": None,
                "purchase_price": 100,
                "current_value": 200,
                "valued_on": None,
                "mortgage_balance": 50,
                "monthly_rent": 0,
                "note": "",
            }
        )
        summary = db.portfolio_summary(date(2026, 10, 30))
        self.assertEqual(summary["property_equity_cny"], 150)
        self.assertEqual(summary["properties"][0]["valued_on"], date.today().isoformat())

        food = next(row for row in db.list_categories() if row["name"] == "餐饮")
        db.save_expense({"category_id": food["id"], "occurred_on": "2026-10-02", "amount": 80, "payee": "", "note": ""})
        db.save_expense({"category_id": food["id"], "occurred_on": "2026-09-02", "amount": 20, "payee": "", "note": ""})
        db.save_budget(2026, food["id"], 1000, "")
        progress = next(row for row in db.budget_progress(2026) if row["category"] == "餐饮")
        self.assertEqual(progress["actual"], 100)
        self.assertEqual(progress["remain"], 900)
        self.assertEqual(db.copy_budget(2026, 2027), 1)
        db.delete_account(account)
        self.assertEqual(db.list_balances(account), [])

    def test_dca(self):
        plan = db.save_dca_plan(
            {
                "name": "沪深300",
                "symbol": "20",
                "amount": 500,
                "cycle": "每月",
                "debit_day": 8,
                "status": "进行中",
                "started_on": "2024-01-08",
                "manual_nav": None,
                "note": "",
            }
        )
        self.assertEqual(db.list_dca_plans()[0]["symbol"], "000020")
        db.save_dca_record(
            {"plan_id": plan, "kind": "期初", "trade_date": "2026-01-01", "amount": 100, "shares": 100, "nav": 1, "note": ""}
        )
        db.save_dca_record(
            {"plan_id": plan, "kind": "定投", "trade_date": "2026-02-08", "amount": 200, "nav": 2, "shares": None, "note": ""}
        )
        rows, total, unpriced = db.dca_plan_rows()
        self.assertEqual(rows[0]["shares"], 200)
        self.assertEqual(rows[0]["cost"], 300)
        self.assertEqual(rows[0]["market_value"], 400)
        self.assertEqual(total, 400)
        self.assertEqual(unpriced, 0)
        db.save_dca_record(
            {"plan_id": plan, "kind": "赎回", "trade_date": "2026-03-01", "amount": 160, "shares": 100, "nav": 1.6, "note": ""}
        )
        db.save_dca_record(
            {"plan_id": plan, "kind": "红利再投", "trade_date": "2026-03-15", "amount": 10, "shares": 10, "nav": 1.5, "note": ""}
        )
        rows, total, _unpriced = db.dca_plan_rows()
        self.assertEqual(rows[0]["shares"], 110)
        self.assertEqual(rows[0]["cost"], 150)
        self.assertEqual(total, 165)
        with self.assertRaises(ValueError):
            db.save_dca_record(
                {"plan_id": plan, "kind": "赎回", "trade_date": "2026-04-01", "amount": 1, "shares": 500, "note": ""}
            )
        path = Path(self.tmp.name) / "hist.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.append(dca.TEMPLATE_HEADERS)
        sheet.append(["2026-04-08", "定投", 100, 2, None, 0, "四月"])
        book.save(path)
        self.assertEqual(db.import_dca_records(plan, path), 1)
        rows, total, _unpriced = db.dca_plan_rows()
        self.assertEqual(rows[0]["shares"], 160)
        self.assertEqual(rows[0]["cost"], 250)
        self.assertEqual(db.portfolio_summary(date(2026, 10, 30))["invest_cny"], total)

    def test_excel(self):
        path = Path(self.tmp.name) / "out.xlsx"
        export_workbook(path)
        wb = load_workbook(path)
        self.assertEqual(
            wb.sheetnames,
            ["总览", "汇率", "账户", "余额快照", "账户明细", "投资持仓", "基金定投", "定投记录", "不动产", "支出", "预算", "行情"],
        )
        self.assertEqual(wb["总览"]["A2"].value, "储蓄")
        self.assertEqual(wb["总览"]["B2"].value, 0)
        account = db.save_account({"name": "现金", "institution": "", "kind": "现金", "currency": "CNY", "note": ""})
        db.save_balance({"account_id": account, "as_of_date": "2026-10-30", "balance": 1000, "note": ""})
        export_workbook(path)
        wb = load_workbook(path)
        self.assertEqual(wb["总览"]["B2"].value, 1000)
        self.assertTrue(wb["总览"]._charts)


if __name__ == "__main__":
    unittest.main()
