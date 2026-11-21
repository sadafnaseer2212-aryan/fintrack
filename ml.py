"""Expense prediction module.

Groups a user's expenses by month and fits scikit-learn regression models
(Linear Regression and Random Forest) on the monthly totals, then predicts the
total for the upcoming month. Linear Regression extrapolates the trend; Random
Forest uses the previous months' totals as features. The final prediction is
the average of the models that could be trained.
"""
from collections import OrderedDict
from datetime import date

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def next_month(key):
    y, m = map(int, key.split("-"))
    return f"{y + (m == 12):04d}-{(m % 12) + 1:02d}"


def monthly_expense_totals(transactions):
    """Return OrderedDict month -> total expense, with empty months filled in."""
    totals = {}
    for t in transactions:
        if t.type == "expense":
            k = month_key(t.date)
            totals[k] = totals.get(k, 0) + t.amount
    if not totals:
        return OrderedDict()
    keys = sorted(totals)
    out, k = OrderedDict(), keys[0]
    while k <= keys[-1]:
        out[k] = round(totals.get(k, 0), 2)
        k = next_month(k)
    return out


def predict_next_month(transactions):
    """Return a dict with the prediction details, or a reason it can't run."""
    totals = monthly_expense_totals(transactions)
    # Exclude the current (incomplete) month from training data.
    current = month_key(date.today())
    history = OrderedDict((k, v) for k, v in totals.items() if k < current)
    target = next_month(current)

    values = list(history.values())
    if len(values) < 2:
        return {"ok": False,
                "reason": "Add expenses for at least 2 past months to get a prediction.",
                "months_available": len(values)}

    y = np.array(values)
    X = np.arange(len(y)).reshape(-1, 1)
    # Months from the first history month to the target month.
    steps_ahead, k = 0, next(iter(history))
    while k < target:
        k, steps_ahead = next_month(k), steps_ahead + 1

    lr = LinearRegression().fit(X, y)
    lr_pred = float(lr.predict([[steps_ahead]])[0])
    preds = {"Linear Regression": lr_pred}

    # Random Forest on lag features (needs a few months of data).
    lags = 2
    if len(y) >= lags + 3:
        Xl = np.array([y[i - lags:i] for i in range(lags, len(y))])
        yl = y[lags:]
        rf = RandomForestRegressor(n_estimators=100, random_state=42).fit(Xl, yl)
        preds["Random Forest"] = float(rf.predict([y[-lags:]])[0])

    final = max(0.0, sum(preds.values()) / len(preds))
    avg = float(y.mean())
    return {
        "ok": True,
        "month": target,
        "prediction": round(final, 2),
        "models": {k: round(max(0.0, v), 2) for k, v in preds.items()},
        "average": round(avg, 2),
        "trend": "up" if lr.coef_[0] > 0 else "down",
        "months_available": len(values),
    }
