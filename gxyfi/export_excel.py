"""把全部台账写成一个 Excel 工作簿。数据表不合并单元格，方便筛选和透视。"""

from __future__ import annotations

from datetime import date, datetime
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

from gxyfi import db

HEADER_FILL = PatternFill("solid", fgColor="1C1914")
HEADER_FONT = Font(name="Calibri", bold=True, color="F4E7C6", size=11)
BODY_FONT = Font(name="Calibri", size=11, color="1C1914")
NOTE_FONT = Font(name="Calibri", size=10, color="6B6560", italic=True)
THIN = Border(
    left=Side(style="thin", color="E6E0D6"),
    right=Side(style="thin", color="E6E0D6"),
    top=Side(style="thin", color="E6E0D6"),
    bottom=Side(style="thin", color="E6E0D6"),
)
MARKET_LABEL = {"US": "美股", "A": "A股", "GOLD": "黄金", "OTHER": "其他"}


def _as_date(value):
    if not value:
        return None
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _visual_len(value):
    total = 0
    for char in "" if value is None else str(value):
        total += 2 if ord(char) > 127 else 1
    return total


def write_table(ws, headers, records, formats=None):
    formats = formats or {}
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="center")
        cell.border = THIN
    widths = [_visual_len(header) + 2 for header in headers]
    for row_index, record in enumerate(records, 2):
        for col, value in enumerate(record, 1):
            cell = ws.cell(row_index, col, value)
            cell.font = BODY_FONT
            cell.border = THIN
            cell.alignment = Alignment(vertical="center")
            if isinstance(value, float):
                cell.number_format = formats.get(col, "#,##0.00")
                cell.alignment = Alignment(vertical="center", horizontal="right")
            elif isinstance(value, date):
                cell.number_format = "YYYY-MM-DD"
            widths[col - 1] = max(widths[col - 1], _visual_len(value) + 2)
    last_row = max(1, len(records) + 1)
    last_col = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"
    ws.row_dimensions[1].height = 22
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = min(max(width, 12), 42)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.5, right=0.5, top=0.7, bottom=0.6, header=0.3, footer=0.3)
    ws.oddHeader.left.text = "gxyFi"
    ws.oddFooter.left.text = "台账导出，不构成投资建议"
    ws.oddFooter.right.text = "第 &P 页"
    ws.sheet_view.showGridLines = False
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"
    ws.print_title_rows = "1:1"


def export_workbook(path):
    summary = db.portfolio_summary()
    wb = Workbook()
    overview = wb.active
    overview.title = "总览"
    headers = ["指标", "金额（人民币）", "备注"]
    net = summary["savings_cny"] + summary["invest_cny"] + summary["property_equity_cny"]
    notes = []
    if summary["savings_missing"]:
        notes.append("有账户缺少汇率，未折入储蓄")
    if summary["invest_missing"]:
        notes.append("有持仓缺少汇率，未折入投资")
    if summary["unpriced"]:
        notes.append(f"{summary['unpriced']} 笔持仓还没有价格")
    if summary["property_missing"]:
        notes.append("有不动产未填现值，未计入净值")
    records = [
        ["储蓄", round(summary["savings_cny"], 2), "各账户最近一次手工余额"],
        ["投资市值", round(summary["invest_cny"], 2), "手工现价优先，否则用最近行情"],
        ["不动产净值", round(summary["property_equity_cny"], 2), "现值减去登记的贷款余额"],
        ["净资产", round(net, 2), "以上三项合计"],
        ["本月支出", round(summary["month_expense"], 2), summary["today"][:7]],
        ["本年预算", round(summary["budget_total"], 2), str(date.today().year)],
        ["本年已支出", round(summary["year_expense"], 2), ""],
    ]
    write_table(overview, headers, records)
    overview["E1"] = "说明"
    overview["E1"].font = HEADER_FONT
    overview["E1"].fill = HEADER_FILL
    overview["E2"] = (
        "本表由 gxyFi 导出，只反映手工台账和最近一次行情估值。"
        "行情有延迟，不构成投资建议。贷款若已在账户里记成负数，就不要在不动产里再填贷款余额。"
    )
    overview["E2"].font = NOTE_FONT
    overview["E2"].alignment = Alignment(wrap_text=True, vertical="top")
    overview.merge_cells("E2:G5")
    overview.column_dimensions["E"].width = 28
    overview.column_dimensions["F"].width = 18
    overview.column_dimensions["G"].width = 18
    if any(row[1] for row in records[:3]):
        chart = BarChart()
        chart.type = "col"
        chart.title = "资产结构"
        chart.style = 10
        chart.y_axis.title = None
        chart.legend = None
        data = Reference(overview, min_col=2, min_row=1, max_row=4)
        cats = Reference(overview, min_col=1, min_row=2, max_row=4)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.shape = 4
        chart.width = 12
        chart.height = 6
        overview.add_chart(chart, "E7")
    overview.sheet_properties.tabColor = "C6A15B"

    fx_headers = ["币种", "每 1 单位折合人民币", "来源", "行情时间"]
    fx_rows = []
    for code, item in summary["fx"].items():
        fx_rows.append([code, item["rate"], item["source"], item["at"]])
    fx_sheet = wb.create_sheet("汇率")
    write_table(fx_sheet, fx_headers, fx_rows, formats={2: "0.0000"})

    account_rows = []
    for row in summary["accounts"]:
        account_rows.append(
            [
                row["name"],
                row["institution"],
                row["kind"],
                row["currency"],
                row["balance"],
                _as_date(row["as_of_date"]),
                None if row["cny"] is None else round(row["cny"], 2),
                row["age"],
                row["note"],
            ]
        )
    write_table(
        wb.create_sheet("账户"),
        ["账户", "机构", "类型", "币种", "最近余额", "截止日期", "折合人民币", "距今天数", "备注"],
        account_rows,
    )

    balance_rows = []
    for row in db.list_all_balances():
        balance_rows.append(
            [
                row["account_name"],
                row["institution"],
                row["currency"],
                _as_date(row["as_of_date"]),
                row["balance"],
                row.get("source") or "手工",
                row["note"],
            ]
        )
    write_table(
        wb.create_sheet("余额快照"),
        ["账户", "机构", "币种", "日期", "余额", "来源", "备注"],
        balance_rows,
    )

    txn_rows = []
    for row in db.list_all_txns():
        txn_rows.append(
            [
                row["account_name"],
                _as_date(row["txn_date"]),
                row["txn_time"],
                row["amount"] if row["amount"] > 0 else None,
                -row["amount"] if row["amount"] < 0 else None,
                row["balance"],
                row["counterparty"],
                row["summary"],
                row["note"],
                row["batch"],
            ]
        )
    write_table(
        wb.create_sheet("账户明细"),
        ["账户", "日期", "时间", "收入", "支出", "交易后余额", "对方户名", "摘要", "备注", "导入批次"],
        txn_rows,
    )

    holding_rows = []
    for row in summary["holdings"]:
        holding_rows.append(
            [
                row["name"],
                row["symbol"],
                MARKET_LABEL.get(row["market"], row["market"]),
                row["quantity"],
                row["currency"],
                row["avg_cost"],
                row["price"],
                row["source"],
                row["quoted_at"],
                None if row["market_value"] is None else round(row["market_value"], 2),
                None if row["cny"] is None else round(row["cny"], 2),
                None if row["pnl"] is None else round(row["pnl"], 2),
                row["account_label"],
                row["note"],
            ]
        )
    write_table(
        wb.create_sheet("投资持仓"),
        ["名称", "代码", "市场", "数量", "币种", "成本价", "现价", "价格来源", "行情时间", "市值（原币）", "市值（人民币）", "浮盈（原币）", "账户", "备注"],
        holding_rows,
        formats={4: "0.0000", 6: "0.0000", 7: "0.0000"},
    )

    plan_rows = []
    for row in summary["dca_plans"]:
        plan_rows.append(
            [
                row["name"],
                row["symbol"],
                row["amount"],
                row["cycle_label"],
                row["status"],
                _as_date(row["started_on"]),
                row["shares"],
                row["cost"],
                row["nav"],
                row["nav_source"],
                row["market_value"],
                row["pnl"],
                row["note"],
            ]
        )
    write_table(
        wb.create_sheet("基金定投"),
        ["基金", "代码", "每期金额", "周期", "状态", "开始日期", "份额", "成本", "净值", "净值来源", "市值", "浮盈", "备注"],
        plan_rows,
        formats={7: "0.0000", 9: "0.0000"},
    )
    record_rows = []
    for row in db.list_dca_records():
        record_rows.append(
            [
                row["plan_name"],
                row["plan_symbol"],
                row["kind"],
                _as_date(row["trade_date"]),
                row["amount"],
                row["nav"],
                row["shares"],
                row["fee"],
                row["note"],
            ]
        )
    write_table(
        wb.create_sheet("定投记录"),
        ["基金", "代码", "类型", "确认日期", "金额", "确认净值", "确认份额", "手续费", "备注"],
        record_rows,
        formats={6: "0.0000", 7: "0.0000"},
    )

    property_rows = []
    for row in summary["properties"]:
        property_rows.append(
            [
                row["name"],
                row["kind"],
                row["city"],
                row["address"],
                row["area_sqm"],
                _as_date(row["acquired_on"]),
                row["purchase_price"],
                row["current_value"],
                _as_date(row["valued_on"]),
                row["mortgage_balance"],
                None if row["equity"] is None else round(row["equity"], 2),
                row["monthly_rent"],
                row["note"],
            ]
        )
    write_table(
        wb.create_sheet("不动产"),
        ["名称", "类型", "城市", "地址", "面积平方米", "购入日", "购入价", "现值", "估值日", "贷款余额", "净值", "月租金", "备注"],
        property_rows,
    )

    expense_rows = []
    for row in db.list_expenses():
        expense_rows.append(
            [
                _as_date(row["occurred_on"]),
                row["category"],
                row["amount"],
                row["payee"],
                row["note"],
            ]
        )
    write_table(wb.create_sheet("支出"), ["日期", "类别", "金额（人民币）", "对象", "备注"], expense_rows)

    budget_rows = []
    for row in db.list_budgets():
        budget_rows.append([row["year"], row["category"], row["amount"], row["note"]])
    write_table(wb.create_sheet("预算"), ["年份", "类别", "年度预算（人民币）", "备注"], budget_rows)

    quote_rows = []
    for row in db.list_quotes():
        quote_rows.append(
            [
                MARKET_LABEL.get(row["market"], row["market"]),
                row["symbol"],
                row["name"],
                row["price"],
                row["prev_close"],
                row["currency"],
                row["quoted_at"],
                row["source"],
                row["fetched_at"],
                "成功" if row["ok"] else "失败",
                row["message"],
            ]
        )
    write_table(
        wb.create_sheet("行情"),
        ["市场", "代码", "名称", "价格", "昨收", "币种", "行情时间", "来源", "本机刷新时间", "状态", "说明"],
        quote_rows,
        formats={4: "0.0000", 5: "0.0000"},
    )

    for sheet in wb.worksheets:
        sheet.page_setup.horizontalCentered = True
        sheet.sheet_view.zoomScale = 110

    wb.properties.creator = "gxyFi"
    wb.properties.title = "gxyFi 家庭资产台账"
    wb.properties.subject = "储蓄、投资、不动产、支出、预算"
    wb.properties.description = "；".join(notes) if notes else "完整台账导出"
    wb.save(path)
    return path
