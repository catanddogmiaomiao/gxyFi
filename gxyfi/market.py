"""家庭持仓规模的行情。只取用户自己的代码，不做全市场抓取。

黄金：上海黄金交易所官网延时行情。
A 股：新浪财经，失败时改用东方财富。
美股：若填写了 Finnhub token 则优先用 Finnhub，否则用 Yahoo Finance。
汇率：Yahoo Finance 的 CNY=X 与 HKDCNY=X。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

SH_PREFIXES = ("60", "68", "69", "50", "51", "52", "56", "58", "11", "90")
SZ_PREFIXES = ("00", "30", "12", "15", "16", "18", "20")
BJ_PREFIXES = ("43", "83", "87", "92")
GOLD_SYMBOLS = {
    "AU(T+D)": "Au(T+D)",
    "MAU(T+D)": "mAu(T+D)",
    "AG(T+D)": "Ag(T+D)",
    "AU99.99": "Au99.99",
    "AU99.95": "Au99.95",
    "AU100G": "Au100g",
    "IAU99.99": "iAu99.99",
}


def to_sina_symbol(symbol: str) -> str:
    text = symbol.strip().lower().replace(" ", "")
    if text.startswith(("sh", "sz", "bj")):
        code = text[2:]
        if code.isdigit() and len(code) == 6:
            return text
        raise ValueError("A 股代码应为 6 位数字，或带 sh/sz/bj 前缀")
    if not (text.isdigit() and len(text) == 6):
        raise ValueError("A 股代码应为 6 位数字，例如 600519")
    if text.startswith(SH_PREFIXES):
        return "sh" + text
    if text.startswith(SZ_PREFIXES):
        return "sz" + text
    if text.startswith(BJ_PREFIXES) or text.startswith(("4", "8")):
        return "bj" + text
    raise ValueError("无法识别的 A 股代码")


def to_yahoo_symbol(symbol: str) -> str:
    text = symbol.strip().upper().replace(".", "-")
    if not re.fullmatch(r"[A-Z0-9^=\-]{1,16}", text):
        raise ValueError("美股代码无效，例如 AAPL 或 BRK.B")
    return text


def canonical_gold(symbol: str) -> str:
    key = symbol.strip().upper().replace(" ", "")
    if key in GOLD_SYMBOLS:
        return GOLD_SYMBOLS[key]
    raise ValueError("黄金代码请用 Au(T+D)、mAu(T+D)、Ag(T+D) 或 Au99.99")


def canonical_symbol(market: str, symbol: str) -> str:
    if market == "A":
        return to_sina_symbol(symbol)[2:]
    if market == "US":
        return to_yahoo_symbol(symbol)
    if market == "GOLD":
        return canonical_gold(symbol)
    text = symbol.strip()
    if not text:
        raise ValueError("请填写代码")
    return text


def prepare_holding(data: dict) -> dict:
    market = data["market"]
    symbol = canonical_symbol(market, data.get("symbol") or "")
    currency = data.get("currency") or "CNY"
    if market == "US":
        currency = "USD"
    elif market in ("A", "GOLD"):
        currency = "CNY"
    name = (data.get("name") or "").strip() or symbol
    return {**data, "name": name, "symbol": symbol, "currency": currency, "market": market}


def _request(url, headers=None, data=None, timeout=12):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def parse_sge_payload(obj: dict) -> dict:
    delay = obj.get("delaystr") or ""
    matched = re.search(r"(\d{2}:\d{2}):\d{2}", delay)
    cutoff = matched.group(1) if matched else None
    last_price = None
    last_time = ""
    for stamp, raw in zip(obj.get("times") or [], obj.get("data") or []):
        if raw in (None, "", "-", "null"):
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if cutoff is not None and str(stamp) >= cutoff:
            continue
        last_price = price
        last_time = str(stamp)
    if last_price is None:
        try:
            now = float(obj.get("now"))
        except (TypeError, ValueError):
            now = 0
        if now > 0:
            last_price = now
    if last_price is None:
        raise ValueError("上金所没有返回有效价格")
    quoted = delay.strip()
    if last_time and quoted:
        quoted = f"{quoted} 最新点 {last_time}"
    return {
        "name": obj.get("heyue") or "",
        "price": last_price,
        "prev_close": None,
        "currency": "CNY",
        "quoted_at": quoted,
    }


def parse_sina_text(text: str) -> dict:
    matched = re.search(r'="(.*)"', text, re.S)
    if not matched or not matched.group(1).strip():
        raise ValueError("新浪没有返回这只代码")
    parts = matched.group(1).split(",")
    if len(parts) < 4:
        raise ValueError("新浪行情字段不完整")
    price = float(parts[3])
    prev = float(parts[2]) if parts[2] else None
    if price <= 0 and prev and prev > 0:
        price = prev
    if price <= 0:
        raise ValueError("新浪返回的价格无效")
    quoted = ""
    if len(parts) > 31 and parts[30]:
        quoted = f"{parts[30]} {parts[31]}".strip()
    return {
        "name": parts[0],
        "price": price,
        "prev_close": prev,
        "currency": "CNY",
        "quoted_at": quoted,
    }


def parse_eastmoney_payload(obj: dict) -> dict:
    data = obj.get("data") or None
    if not data or data.get("f43") in (None, "-"):
        raise ValueError("东方财富没有返回这只代码")
    scale = 10 ** int(data.get("f59") or 2)
    price = float(data["f43"]) / scale
    prev = None if data.get("f60") in (None, "-") else float(data["f60"]) / scale
    if price <= 0:
        raise ValueError("东方财富返回的价格无效")
    return {
        "name": data.get("f58") or "",
        "price": price,
        "prev_close": prev,
        "currency": "CNY",
        "quoted_at": "",
    }


def parse_yahoo_chart(obj: dict) -> dict:
    result = ((obj.get("chart") or {}).get("result") or [None])[0]
    if not result:
        error = ((obj.get("chart") or {}).get("error") or {}).get("description") or "Yahoo 没有返回行情"
        raise ValueError(error)
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        raise ValueError("Yahoo 没有返回最新价")
    prev = meta.get("chartPreviousClose", meta.get("previousClose"))
    stamp = meta.get("regularMarketTime")
    quoted = ""
    if stamp:
        quoted = datetime.fromtimestamp(int(stamp), tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return {
        "name": meta.get("shortName") or meta.get("symbol") or "",
        "price": float(price),
        "prev_close": None if prev is None else float(prev),
        "currency": meta.get("currency") or "",
        "quoted_at": quoted,
    }


def parse_finnhub_quote(obj: dict) -> dict:
    price = obj.get("c")
    if not price:
        raise ValueError("Finnhub 没有返回最新价")
    prev = obj.get("pc")
    stamp = obj.get("t")
    quoted = ""
    if stamp:
        quoted = datetime.fromtimestamp(int(stamp), tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return {
        "name": "",
        "price": float(price),
        "prev_close": None if not prev else float(prev),
        "currency": "USD",
        "quoted_at": quoted,
    }


def _quote(market, symbol, name, price, prev, currency, quoted_at, source, ok, message):
    return {
        "market": market,
        "symbol": symbol,
        "name": name,
        "price": price,
        "prev_close": prev,
        "currency": currency,
        "quoted_at": quoted_at,
        "source": source,
        "ok": ok,
        "message": message,
    }


def _fail(market, symbol, message):
    return _quote(market, symbol, "", None, None, "", "", "", False, message)


def fetch_gold(symbol: str) -> dict:
    canonical = canonical_gold(symbol)
    body = f"instid={quote(canonical, safe='()')}".encode("ascii")
    raw = _request(
        "https://www.sge.com.cn/graph/quotations",
        headers={
            "User-Agent": UA,
            "Referer": "https://www.sge.com.cn/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        data=body,
    )
    parsed = parse_sge_payload(json.loads(raw.decode("utf-8")))
    parsed["name"] = parsed["name"] or canonical
    return parsed | {"source": "上海黄金交易所（延时）"}


def fetch_a_share(symbol: str) -> dict:
    sina_symbol = to_sina_symbol(symbol)
    sina_error = "新浪不可用"
    try:
        raw = _request(
            f"https://hq.sinajs.cn/list={sina_symbol}",
            headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"},
        )
        parsed = parse_sina_text(raw.decode("gbk", errors="replace"))
        return parsed | {"source": "新浪财经"}
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        sina_error = str(exc)
    prefix = sina_symbol[:2]
    code = sina_symbol[2:]
    secid = f"{1 if prefix == 'sh' else 0}.{code}"
    raw = _request(
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields=f43,f57,f58,f60,f59",
        headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
    )
    parsed = parse_eastmoney_payload(json.loads(raw.decode("utf-8")))
    parsed["quoted_at"] = parsed["quoted_at"] or "回退源，时间以本机刷新为准"
    return parsed | {"source": f"东方财富（新浪失败：{sina_error}）"}


def fetch_us(symbol: str, finnhub_token: str = "") -> dict:
    yahoo_symbol = to_yahoo_symbol(symbol)
    finnhub_error = ""
    if finnhub_token.strip():
        try:
            raw = _request(
                "https://finnhub.io/api/v1/quote"
                f"?symbol={quote(yahoo_symbol)}&token={quote(finnhub_token.strip())}"
            )
            parsed = parse_finnhub_quote(json.loads(raw.decode("utf-8")))
            parsed["name"] = yahoo_symbol
            return parsed | {"source": "Finnhub"}
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            finnhub_error = str(exc)
    raw = _request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo_symbol)}?interval=1d&range=5d",
        headers={"User-Agent": UA},
    )
    parsed = parse_yahoo_chart(json.loads(raw.decode("utf-8")))
    source = "Yahoo Finance"
    if finnhub_error:
        source = f"Yahoo Finance（Finnhub 失败：{finnhub_error}）"
    return parsed | {"source": source}


def fetch_fx_pair(yahoo_symbol: str) -> dict:
    raw = _request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo_symbol)}?interval=1d&range=1d",
        headers={"User-Agent": UA},
    )
    return parse_yahoo_chart(json.loads(raw.decode("utf-8")))


def fetch_one(market: str, symbol: str, finnhub_token: str = "") -> dict:
    try:
        if market == "GOLD":
            parsed = fetch_gold(symbol)
            canonical = canonical_gold(symbol)
            currency = "CNY"
        elif market == "A":
            parsed = fetch_a_share(symbol)
            canonical = to_sina_symbol(symbol)[2:]
            currency = "CNY"
        elif market == "US":
            parsed = fetch_us(symbol, finnhub_token)
            canonical = to_yahoo_symbol(symbol)
            currency = parsed.get("currency") or "USD"
        else:
            return _fail(market, symbol, "其他资产请填写手工现价")
        return _quote(
            market,
            canonical,
            parsed.get("name") or canonical,
            parsed.get("price"),
            parsed.get("prev_close"),
            currency,
            parsed.get("quoted_at") or "",
            parsed.get("source") or "",
            True,
            "",
        )
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        try:
            shown = canonical_symbol(market, symbol)
        except ValueError:
            shown = symbol
        return _fail(market, shown, str(exc))


def fetch_refresh(holdings, finnhub_token=""):
    seen = set()
    quotes = []
    for holding in holdings:
        market = holding.get("market")
        symbol = (holding.get("symbol") or "").strip()
        if market not in ("US", "A", "GOLD") or not symbol:
            continue
        key = (market, symbol)
        if key in seen:
            continue
        seen.add(key)
        quotes.append(fetch_one(market, symbol, finnhub_token))
    fx_updates = {}
    errors = []
    for code, yahoo_symbol in (("USD", "CNY=X"), ("HKD", "HKDCNY=X")):
        try:
            parsed = fetch_fx_pair(yahoo_symbol)
            fx_updates[code] = {
                "rate": parsed["price"],
                "source": "Yahoo Finance",
                "at": parsed.get("quoted_at") or "",
            }
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{code}: {exc}")
    return {"quotes": quotes, "fx_updates": fx_updates, "errors": errors}
