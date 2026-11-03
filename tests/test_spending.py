import unittest
from datetime import date

from gxyfi import spending


def expense(day, category_id, category, amount):
    return {"occurred_on": day, "category_id": category_id, "category": category, "amount": amount}


ROWS = [
    expense("2026-10-28", 2, "餐饮", 21.89),
    expense("2026-10-30", 2, "餐饮", 38.40),
    expense("2026-11-01", 21, "税费", 2657.80),
    expense("2026-11-01", 3, "交通", 100.00),
    expense("2026-11-01", 2, "餐饮", 20.54),
    expense("2026-11-02", 2, "餐饮", -5.00),
    expense("2026-11-02", 10, "其他", 10.00),
]


class SpendingTest(unittest.TestCase):
    def test_by_category_nets_refunds_and_sorts(self):
        rows = spending.by_category(ROWS, "2026-11")
        self.assertEqual([row["category"] for row in rows], ["税费", "交通", "餐饮", "其他"])
        food = next(row for row in rows if row["category"] == "餐饮")
        self.assertEqual((food["amount"], food["count"], food["refund"]), (15.54, 2, 5.0))
        self.assertAlmostEqual(sum(row["share"] for row in rows), 1.0)
        self.assertEqual(spending.total(ROWS, "2026-11"), 2783.34)
        self.assertEqual(spending.total(ROWS), 2843.63)

    def test_periods(self):
        self.assertEqual(spending.previous_period("2026-01"), "2025-12")
        self.assertEqual(spending.previous_period("2026-11"), "2026-10")
        self.assertEqual(spending.previous_period("2026"), "2025")
        self.assertIsNone(spending.previous_period(None))
        self.assertEqual(spending.period_label("2026-03"), "2026 年 3 月")

    def test_monthly_stack_merges_small_categories(self):
        keys, names, cells = spending.monthly_stack(ROWS, 3, top=2, today=date(2026, 11, 3))
        self.assertEqual(keys, ["2026-09", "2026-10", "2026-11"])
        self.assertEqual(names, ["税费", "交通", spending.OTHER])
        self.assertEqual(cells["2026-10"], {spending.OTHER: 60.29})
        self.assertEqual(cells["2026-11"], {"税费": 2657.8, "交通": 100.0, spending.OTHER: 25.54})
        self.assertEqual(cells["2026-09"], {})

    def test_budget_for_period(self):
        budgets = [{"category_id": 2, "planned": 1200}, {"category_id": 3, "planned": 0}]
        self.assertEqual(spending.budget_for("2026-11", budgets), {2: 100.0})
        self.assertEqual(spending.budget_for("2026", budgets), {2: 1200})
        self.assertEqual(spending.budget_for(None, budgets), {})


if __name__ == "__main__":
    unittest.main()
