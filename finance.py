"""Pure calculation helpers (no Flask): month keys, totals, budgets, alerts."""
from collections import defaultdict
from datetime import date


def mkey(d):
    return f"{d.year:04d}-{d.month:02d}"


def current_month():
    return mkey(date.today())


def add_months(key, n):
    y, m = map(int, key.split("-"))
    t = y * 12 + (m - 1) + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def valid_month(key):
    try:
        y, m = map(int, key.split("-"))
        return 1900 < y < 3000 and 1 <= m <= 12 and len(key) == 7
    except (AttributeError, ValueError):
        return False


def month_label(key, fmt="%b %Y"):
    y, m = map(int, key.split("-"))
    return date(y, m, 1).strftime(fmt)


def month_range(start, end):
    out, k = [], start
    while k <= end:
        out.append(k)
        k = add_months(k, 1)
    return out


def totals_by_month(txs, tx_type):
    out = defaultdict(float)
    for t in txs:
        if t.type == tx_type:
            out[mkey(t.date)] += t.amount
    return out


def category_totals(txs, month=None, tx_type="expense"):
    out = defaultdict(float)
    for t in txs:
        if t.type == tx_type and (month is None or mkey(t.date) == month):
            out[t.category] += t.amount
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def pct_change(cur, prev):
    """Percentage change, or None when there is no previous value to compare to."""
    if not prev:
        return None
    return (cur - prev) / prev * 100


def effective_budgets(budgets, month):
    """{category: amount} for a month; month-specific budgets override recurring ones."""
    out = {}
    for b in budgets:
        if b.month is None:
            out.setdefault(b.category, b.amount)
    for b in budgets:
        if b.month == month:
            out[b.category] = b.amount
    return out


def budget_status(txs, budgets, month):
    spent = category_totals(txs, month)
    rows = []
    for cat, limit in sorted(effective_budgets(budgets, month).items()):
        s = spent.get(cat, 0.0)
        pct = s / limit * 100 if limit else 0
        rows.append({"category": cat, "budget": limit, "spent": s, "remaining": limit - s,
                     "pct": pct, "over": s > limit, "near": 80 <= pct <= 100})
    return rows


def build_alerts(txs, budgets, overall_budget, month, prediction=None):
    """Return a list of {level, title, text} alerts for the given month."""
    alerts = []
    month_spent = sum(t.amount for t in txs if t.type == "expense" and mkey(t.date) == month)

    if overall_budget and month_spent > overall_budget:
        alerts.append({"level": "danger", "title": "Monthly budget exceeded",
                       "text": f"Spent Rs {month_spent:,.0f} of your Rs {overall_budget:,.0f} limit "
                               f"(+Rs {month_spent - overall_budget:,.0f})."})

    for row in budget_status(txs, budgets, month):
        if row["over"]:
            alerts.append({"level": "danger", "title": f"{row['category']} budget exceeded",
                           "text": f"Rs {row['spent']:,.0f} spent of Rs {row['budget']:,.0f} "
                                   f"({row['pct']:.0f}%)."})
        elif row["near"]:
            alerts.append({"level": "warning", "title": f"{row['category']} budget nearly used",
                           "text": f"{row['pct']:.0f}% used, Rs {row['remaining']:,.0f} left."})

    # Category spending well above its usual level (average of the previous 3 months).
    prev_months = [add_months(month, -i) for i in range(1, 4)]
    by_cat = defaultdict(lambda: defaultdict(float))
    for t in txs:
        if t.type == "expense":
            by_cat[t.category][mkey(t.date)] += t.amount
    for cat, months in by_cat.items():
        past = [months.get(m, 0.0) for m in prev_months]
        avg = sum(past) / 3
        now = months.get(month, 0.0)
        if avg >= 500 and now > avg * 1.2:
            alerts.append({"level": "warning", "title": f"High {cat} spending",
                           "text": f"Rs {now:,.0f} this month vs. usual Rs {avg:,.0f} "
                                   f"(+{(now / avg - 1) * 100:.0f}%)."})

    if prediction and prediction.get("ok") and overall_budget and \
            prediction["prediction"] > overall_budget:
        alerts.append({"level": "info", "title": "Forecast above budget",
                       "text": f"Next month is forecast at Rs {prediction['prediction']:,.0f}, "
                               f"above your Rs {overall_budget:,.0f} budget."})
    return alerts
