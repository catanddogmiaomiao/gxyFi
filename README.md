# gxyFi

Windows 上的家庭资产台账。用 PyQt 做界面，数据放在本机 SQLite，全部可以导出为一个 Excel 工作簿。

它统计五件事：

- 储蓄：账户余额由你定时手工录入，程序不连接银行。
- 投资：持仓乘以现价。填了手工现价就用手工现价。
- 不动产：现值减去你登记的贷款余额。
- 支出：按月查看。
- 预算：按日历年和支出类别。

网站在另一个仓库 `gxyFi-web`。两边目前各记各的，不会自动同步。iPhone 客户端还没有做，以后对接网站的接口。

行情只用于给持仓估值，有延迟，不构成投资建议。

## 运行

需要 Python 3.12。

```bat
run.bat
```

或：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m gxyfi
```

数据库在 `%LOCALAPPDATA%\gxyFi\gxyfi.db`。备份这个文件即可保留全部手工记录和最近一次行情。

## 行情从哪里来

只查询你持仓里的代码，不做全市场抓取。2026-10-30 在这台电脑上确认过下面三个入口能返回价格。

| 用途 | 采用 | 原因 |
| --- | --- | --- |
| 黄金，尤其是 Au(T+D) | 上海黄金交易所官网 `https://www.sge.com.cn/graph/quotations` | 官方、免费、无需密钥。返回的是延时行情，单位是人民币/克。持仓数量按克填写。 |
| A 股 | 新浪财经 `https://hq.sinajs.cn`，失败时改用东方财富 `push2.eastmoney.com` | 免费、无需密钥。家庭持仓是一只一只查，不会并发扫全市场。新浪需要带 Referer，响应是 GBK。 |
| 美股 | 默认 Yahoo Finance `query1.finance.yahoo.com` 的 chart 接口 | 免费、无需密钥，能覆盖股票和 ETF。这不是雅虎的正式开放接口，可能限流或改字段。 |
| 美元、港币汇率 | Yahoo Finance 的 `CNY=X` 和 `HKDCNY=X` | 和美股同一条免费通道。刷新行情时会覆盖手工汇率。 |

可选：在「行情」页保存 Finnhub token 后，美股优先走 Finnhub 免费档（大约每分钟 60 次，个人使用）。历史 K 线不在这个免费档里，本程序只需要最新价。Token 只留在本机，导出的 Excel 不含 Token。

这些免费来源都没有服务等级承诺。最后一次成功的价格会留在本机；刷新失败时，总览仍用上次的价格或手工现价。

没有采用 AkShare 作为安装依赖。它能包住上面这些站点，但体积大，而且上游一改它就跟着坏。这里直接请求上面三个已经核对过的地址。如果以后要做全市场日线落库，再考虑 Tushare Pro 这类有明确频次规则的源；给家庭持仓估值用不到那一层。

## Excel

右上角「导出 Excel」生成一个工作簿，工作表为：总览、汇率、账户、余额快照、投资持仓、不动产、支出、预算、行情。

数据表第一行是表头，下面每行一条记录，数字是数字、日期是日期，并带筛选和冻结窗格，方便在 Excel 里继续筛选或透视。总览里的说明文字不参与筛选。

## 测试

```bat
.venv\Scripts\python.exe -m unittest tests.test_core
```
