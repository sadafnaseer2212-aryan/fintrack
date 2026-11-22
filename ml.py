"""Expense prediction module (scikit-learn).

Workflow
--------
1. Aggregate the user's expenses into monthly totals. Months between the first
   record and the last *completed* month that have no expenses count as 0.
   The current month is excluded because it is not finished yet.
2. Because the current month is incomplete, forecasting "next month" from the
   last completed month is a 2-step-ahead problem (horizon = 2).
3. Three candidate models:
     * Baseline            - average of the last 3 months (naive benchmark)
     * Linear Regression   - fits the long-term trend (month index -> total)
     * Random Forest       - learns from the previous 3 months' totals (lag
                             features), forecasting recursively
4. Chronological walk-forward evaluation (no shuffling, no future data): for
   each of the last TEST_MONTHS months, every model is trained only on data that
   would have been available 2 months earlier and forecasts that month.
   MAE, RMSE and MAPE are computed on these out-of-sample forecasts.
5. The model with the lowest MAE is selected, retrained on all completed months
   and used to forecast next month. RMSE gives the expected error range.
If there is too little history, no evaluated prediction is shown.
"""
from collections import OrderedDict

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

from finance import add_months, current_month, mkey

HORIZON = 2          # last completed month -> next calendar month
LAGS = 3             # Random Forest uses the previous 3 monthly totals
TEST_MONTHS = 4      # months used for out-of-sample evaluation
MIN_MONTHS_SIMPLE = 3                    # below this: no forecast at all
MIN_MONTHS_EVAL = 9                      # gives at least 2 out-of-sample test months


# ------------------------------------------------------------------ data
def monthly_history(transactions):
    """OrderedDict month -> total expenses for completed months only."""
    totals = {}
    for t in transactions:
        if t.type == "expense":
            k = mkey(t.date)
            totals[k] = totals.get(k, 0.0) + float(t.amount)
    last_complete = add_months(current_month(), -1)
    keys = [k for k in totals if k <= last_complete]
    if not keys:
        return OrderedDict()
    out, k = OrderedDict(), min(keys)
    while k <= last_complete:
        out[k] = round(totals.get(k, 0.0), 2)
        k = add_months(k, 1)
    return out


# ------------------------------------------------------------------ models
def _baseline(train, steps):
    return float(np.mean(train[-3:]))


def _linear(train, steps):
    X = np.arange(len(train)).reshape(-1, 1)
    model = LinearRegression().fit(X, train)
    return float(model.predict([[len(train) - 1 + steps]])[0])


def _random_forest(train, steps):
    series = list(train)
    X = np.array([series[i - LAGS:i] for i in range(LAGS, len(series))])
    y = np.array(series[LAGS:])
    model = RandomForestRegressor(n_estimators=80, max_depth=4, random_state=42).fit(X, y)
    pred = None
    for _ in range(steps):  # recursive multi-step forecast
        pred = float(model.predict([series[-LAGS:]])[0])
        series.append(pred)
    return pred


MODELS = OrderedDict([
    ("Baseline (3-month average)", (_baseline, 3)),
    ("Linear Regression", (_linear, 3)),
    ("Random Forest", (_random_forest, LAGS + 3)),
])  # name -> (forecast function, minimum training months)


def _metrics(actual, predicted):
    a, p = np.array(actual), np.array(predicted)
    err = p - a
    nz = a != 0
    return {"mae": float(np.mean(np.abs(err))),
            "rmse": float(np.sqrt(np.mean(err ** 2))),
            "mape": float(np.mean(np.abs(err[nz] / a[nz])) * 100) if nz.any() else None}


def evaluate(y, months):
    """Walk-forward evaluation. Returns {model: {metrics, preds}} and test months."""
    n = len(y)
    first_test = max(n - TEST_MONTHS, HORIZON - 1 + MODELS["Random Forest"][1])
    test_idx = list(range(first_test, n))
    results = {}
    for name, (fn, min_train) in MODELS.items():
        preds, actual = [], []
        for i in test_idx:
            train = y[: i - HORIZON + 1]   # only data known HORIZON months before month i
            if len(train) < min_train:
                break
            preds.append(max(0.0, fn(train, HORIZON)))
            actual.append(float(y[i]))
        if len(preds) == len(test_idx) and preds:
            results[name] = {**_metrics(actual, preds), "preds": [round(v, 2) for v in preds]}
    return results, [months[i] for i in test_idx], [float(y[i]) for i in test_idx]


# ------------------------------------------------------------------ main entry
_cache = {}


def predict_next_month(transactions, use_cache_key=None):
    if use_cache_key is not None and use_cache_key in _cache:
        return _cache[use_cache_key]
    result = _predict(transactions)
    if use_cache_key is not None:
        if len(_cache) > 200:
            _cache.clear()
        _cache[use_cache_key] = result
    return result


def _predict(transactions):
    hist = monthly_history(transactions)
    months, y = list(hist.keys()), np.array(list(hist.values()), dtype=float)
    target = add_months(current_month(), 1)
    base = {"target": target, "months_available": len(y),
            "history": {"months": months, "values": [round(float(v), 2) for v in y]}}

    if len(y) < MIN_MONTHS_SIMPLE:
        return {**base, "ok": False, "evaluated": False,
                "reason": f"At least {MIN_MONTHS_SIMPLE} completed months of expenses are "
                          f"needed for a forecast (you have {len(y)})."}

    last_month_total = float(y[-1])
    avg3 = float(np.mean(y[-3:]))

    if len(y) < MIN_MONTHS_EVAL:
        # Not enough data to evaluate models: give a clearly-labelled simple forecast.
        pred = max(0.0, _baseline(y, HORIZON))
        return {**base, "ok": True, "evaluated": False, "model": "Baseline (3-month average)",
                "prediction": round(pred, 2), "low": None, "high": None,
                "last_month": last_month_total, "avg3": avg3,
                "change_pct": (pred - last_month_total) / last_month_total * 100 if last_month_total else None,
                "note": f"Low confidence: only {len(y)} months of history. Models are evaluated "
                        f"once {MIN_MONTHS_EVAL} completed months are available."}

    results, test_months, test_actual = evaluate(y, months)
    best = min(results, key=lambda k: results[k]["mae"])
    raw_preds = {name: max(0.0, MODELS[name][0](y, HORIZON)) for name in results}
    pred = raw_preds[best]
    all_preds = {name: round(v, 2) for name, v in raw_preds.items()}
    rmse = results[best]["rmse"]
    return {**base, "ok": True, "evaluated": True, "model": best,
            "prediction": round(pred, 2),
            "low": round(max(0.0, pred - rmse), 2), "high": round(pred + rmse, 2),
            "last_month": last_month_total, "avg3": avg3,
            "change_pct": (pred - last_month_total) / last_month_total * 100 if last_month_total else None,
            "metrics": {k: {m: v for m, v in r.items() if m != "preds"} for k, r in results.items()},
            "all_predictions": all_preds,
            "backtest": {"months": test_months, "actual": [round(v, 2) for v in test_actual],
                         "preds": {k: r["preds"] for k, r in results.items()}},
            "note": f"Evaluated on the last {len(test_months)} months with a chronological "
                    f"walk-forward test; {best} had the lowest error."}
