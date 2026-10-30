def fmt_price(value):
    if value is None:
        return "—"
    text = f"{value:,.4f}"
    return text.rstrip("0").rstrip(".")


def fmt_money(value, digits=2):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def fmt_cny(value):
    if value is None:
        return "—"
    return "¥" + fmt_money(value)


def fmt_signed(value):
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return sign + fmt_money(value)


def fmt_pct(value):
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def pnl_kind(value):
    if value is None or abs(value) < 1e-9:
        return "flat"
    return "up" if value > 0 else "down"
