import os
import tempfile
import unittest
from pathlib import Path

from gxyfi import cloud, db

KEYS = {
    "account": "accounts",
    "balance": "balances",
    "holding": "holdings",
    "property": "properties",
    "category": "categories",
    "expense": "expenses",
    "budget": "budgets",
    "dca_plan": "dcaPlans",
    "dca_record": "dcaRecords",
}


class FakeSite:
    """按网页端 /api/mutate 的约定返回整本账，不联网。"""

    def __init__(self):
        self.state = {key: [] for key in KEYS.values()}
        self.state.update(quotes=[], fx=[])
        self.next_id = 100
        self.calls = []
        self.token_ok = True
        for name in ("餐饮", "居住"):
            self._insert("categories", {"name": name, "sort_order": 0})

    def _insert(self, key, data):
        self.next_id += 1
        row = {"id": self.next_id, **data}
        self.state[key].append(row)
        return row

    def __call__(self, url, path, token="", body=None, method=None):
        self.calls.append((path, body))
        if path == "/api/login":
            return FakeHeaders(), {"ok": True}
        if not self.token_ok:
            raise cloud.AuthExpired(cloud.EXPIRED_TEXT)
        if path == "/api/mutate":
            self.mutate(body)
        return {}, {key: [dict(row) for row in rows] for key, rows in self.state.items()}

    def mutate(self, body):
        key = KEYS[body["entity"]]
        rows = self.state[key]
        data = body["data"]
        if body["op"] == "delete":
            self.state[key] = [row for row in rows if row["id"] != body["id"]]
            return
        if key == "balances":
            same = [row for row in rows if (row["account_id"], row["as_of_date"]) == (data["account_id"], data["as_of_date"])]
            if same:
                same[0].update(data)
                return
        if key == "budgets":
            same = [row for row in rows if (row["year"], row["category_id"]) == (data["year"], data["category_id"])]
            if same:
                same[0].update(data)
                return
        if body["op"] == "update":
            next(row for row in rows if row["id"] == body["id"]).update(data)
            return
        defaults = {"note": "", "created_at": "2026-11-02 09:00:00"}
        if key == "dcaRecords":
            data = {**data, "shares": data.get("shares") or 0}
        self._insert(key, {**defaults, **data})


class FakeHeaders:
    def get_all(self, _name):
        return ["gxyfi_session=tok123; Path=/; HttpOnly; Secure"]


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GXYFI_DB"] = str(Path(self.tmp.name) / "t.db")
        db.init_db()
        self.site = FakeSite()
        self._real = cloud._request
        cloud._request = self.site

    def tearDown(self):
        cloud._request = self._real
        self.tmp.cleanup()
        os.environ.pop("GXYFI_DB", None)

    def connect(self):
        token = cloud.login("https://example.test", "gxy", "pw")
        self.assertEqual(token, "tok123")
        cloud.save_login("https://example.test", "gxy", token)
        cloud.apply(cloud.fetch_state())

    def test_writes_go_to_site_and_mirror(self):
        local_id = db.save_account({"name": "本机", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        self.connect()
        self.assertEqual(db.list_accounts(), [])
        account_id = db.save_account({"name": "招行", "institution": "招商银行", "kind": "储蓄", "currency": "CNY", "note": ""})
        self.assertGreater(account_id, 100)
        self.assertNotEqual(account_id, local_id)
        db.save_balance({"account_id": account_id, "as_of_date": "2026-11-01", "balance": 1000, "note": ""})
        self.assertEqual(self.site.state["balances"][0]["balance"], 1000)
        self.assertEqual(db._one("SELECT balance FROM balance_entries")["balance"], 1000)
        names = [row["name"] for row in db.list_categories()]
        self.assertEqual(names, ["餐饮", "居住"])

        self.site.token_ok = False
        with self.assertRaises(cloud.AuthExpired):
            db.delete_account(account_id)
        self.assertTrue(cloud.expired())
        with self.assertRaises(cloud.AuthExpired):
            db.save_property({"name": "房", "current_value": 1})
        self.assertEqual(len(db.list_accounts()), 1)

    def test_first_login_uploads_selected_records(self):
        mine = db.save_account({"name": "工行", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        db.save_balance({"account_id": mine, "as_of_date": "2026-10-01", "balance": 500, "note": ""})
        renamed = db.save_account({"name": "老名字", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        db.save_balance({"account_id": renamed, "as_of_date": "2026-11-01", "balance": 777, "note": ""})
        fresh = db.save_account({"name": "只在电脑", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        db.save_balance({"account_id": fresh, "as_of_date": "2026-11-01", "balance": 9, "note": ""})
        db.save_property({"name": "老房子", "kind": "自住", "current_value": 3000000})
        category = db._one("SELECT id FROM expense_categories WHERE name='餐饮'")["id"]
        db.save_expense({"occurred_on": "2026-10-02", "amount": 88, "category_id": category, "payee": "面馆", "method": "", "note": ""})
        db.save_budget(2026, category, 12000)

        site_bank = self.site._insert("accounts", {"name": "工行", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        site_other = self.site._insert("accounts", {"name": "新名字", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        self.site._insert("balances", {"account_id": site_other["id"], "as_of_date": "2026-11-02", "balance": 777, "note": ""})

        local = db.local_snapshot()
        items, matches = cloud.local_only(local, self.site("", "/api/state")[1])
        self.assertEqual(matches["accounts"], {mine: site_bank["id"], renamed: site_other["id"]})
        kinds = {(item["kind"], item["checked"]) for item in items}
        self.assertIn(("account", True), kinds)
        self.assertIn(("balance", False), kinds)
        self.assertIn(("property", True), kinds)
        self.assertEqual([item["row"]["name"] for item in items if item["kind"] == "account"], ["只在电脑"])

        self.connect()
        chosen = [item for item in items if item["checked"]]
        sent = cloud.upload(chosen, local, matches)
        self.assertEqual(sent, len(chosen))
        site = self.site.state
        uploaded = next(row for row in site["accounts"] if row["name"] == "只在电脑")
        self.assertTrue(any(row["account_id"] == uploaded["id"] and row["balance"] == 9 for row in site["balances"]))
        self.assertFalse(any(row["balance"] == 500 for row in site["balances"]))
        site_food = next(row["id"] for row in site["categories"] if row["name"] == "餐饮")
        self.assertEqual(site["expenses"][0]["category_id"], site_food)
        self.assertEqual(site["budgets"][0]["category_id"], site_food)
        self.assertEqual([row["name"] for row in site["properties"]], ["老房子"])
        self.assertEqual(len(db.list_accounts()), 3)

    def test_logout_clears_cache(self):
        self.connect()
        db.save_account({"name": "招行", "institution": "", "kind": "储蓄", "currency": "CNY", "note": ""})
        cloud.disconnect()
        self.assertFalse(cloud.enabled())
        self.assertEqual(db.list_accounts(), [])
        self.assertTrue(db.list_categories())
        self.assertEqual(len(self.site.state["accounts"]), 1)

    def test_server_url(self):
        self.assertEqual(cloud.server_url("gxyfi.example.com/abc"), "https://gxyfi.example.com")
        self.assertEqual(cloud.server_url("http://localhost:8787"), "http://localhost:8787")
        with self.assertRaises(cloud.CloudError):
            cloud.server_url("http://gxyfi.example.com")


if __name__ == "__main__":
    unittest.main()
