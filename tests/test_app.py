"""Automated tests for FinTrack.  Run with:  python -m unittest discover tests -v"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from app import app  # noqa: E402
from finance import add_months, budget_status, build_alerts, current_month  # noqa: E402
from ml import evaluate, monthly_history, predict_next_month  # noqa: E402
from models import Budget, PredictionLog, Transaction, User, db  # noqa: E402


def month_date(offset, day=10):
    k = add_months(current_month(), offset)
    return date(int(k[:4]), int(k[5:]), day)


class T:
    """Minimal transaction stand-in for pure-function tests."""
    def __init__(self, d, amount, tx_type="expense", category="Food"):
        self.date, self.amount, self.type, self.category = d, amount, tx_type, category


class Base(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, CSRF_ENABLED=False)
        self.c = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
        self.register("ali", "ali@test.com")

    def register(self, username, email, password="secret1"):
        return self.c.post("/register", data={"username": username, "email": email,
                                              "password": password, "confirm": password})

    def add(self, **kw):
        data = {"type": "expense", "category": "Food", "amount": "100",
                "date": date.today().isoformat(), "description": ""}
        data.update({k: str(v) for k, v in kw.items()})
        return self.c.post("/transactions/new", data=data, follow_redirects=True)

    def uid(self, username="ali"):
        with app.app_context():
            return User.query.filter_by(username=username).first().id


# ------------------------------------------------------------------ auth & security
class AuthTests(Base):
    def test_protected_pages_redirect_to_login(self):
        self.c.post("/logout")
        for url in ["/dashboard", "/transactions", "/budgets", "/reports", "/prediction",
                    "/data", "/settings", "/export/csv", "/backup"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])

    def test_password_is_hashed(self):
        with app.app_context():
            u = User.query.filter_by(username="ali").first()
            self.assertNotEqual(u.password_hash, "secret1")
            self.assertTrue(u.check_password("secret1"))

    def test_login_logout_and_bad_login(self):
        self.c.post("/logout")
        r = self.c.post("/login", data={"username": "ali", "password": "wrong"})
        self.assertIn(b"Invalid username or password", r.data)
        r = self.c.post("/login", data={"username": "ali@test.com", "password": "secret1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/dashboard").status_code, 200)

    def test_open_redirect_blocked(self):
        self.c.post("/logout")
        r = self.c.post("/login?next=https://evil.example.com", data={"username": "ali", "password": "secret1"})
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))
        self.c.post("/logout")
        r = self.c.post("/login?next=/reports", data={"username": "ali", "password": "secret1"})
        self.assertTrue(r.headers["Location"].endswith("/reports"))

    def test_registration_validation(self):
        self.c.post("/logout")
        self.assertIn(b"already exists", self.register("ali", "other@test.com").data)
        self.assertIn(b"valid email", self.register("bob", "not-an-email").data)
        r = self.c.post("/register", data={"username": "bob", "email": "b@test.com", "password": "abcdef", "confirm": "xyz"})
        self.assertIn(b"do not match", r.data)

    def test_csrf_rejects_post_without_token(self):
        app.config["CSRF_ENABLED"] = True
        r = self.c.post("/transactions/new", data={"type": "expense", "category": "Food", "amount": "5"})
        self.assertEqual(r.status_code, 400)
        # with the session token it works
        self.c.get("/transactions/new")
        with self.c.session_transaction() as s:
            token = s["_csrf"]
        r = self.c.post("/transactions/new", data={"csrf_token": token, "type": "expense", "category": "Food",
                                                   "amount": "5", "date": date.today().isoformat()})
        self.assertEqual(r.status_code, 302)

    def test_users_cannot_access_each_others_data(self):
        self.add(amount=999, description="Ali private")
        with app.app_context():
            tx_id = Transaction.query.first().id
        self.c.post("/logout")
        self.register("sara", "sara@test.com")
        self.assertEqual(self.c.get(f"/transactions/{tx_id}").status_code, 404)
        self.assertEqual(self.c.get(f"/transactions/{tx_id}/edit").status_code, 404)
        self.assertEqual(self.c.post(f"/transactions/{tx_id}/delete").status_code, 404)
        self.assertNotIn(b"Ali private", self.c.get("/transactions").data)
        self.assertNotIn(b"Ali private", self.c.get("/export/csv").data)
        self.assertEqual(json.loads(self.c.get("/backup").data)["transactions"], [])
        with app.app_context():
            self.assertIsNotNone(db.session.get(Transaction, tx_id))

    def test_change_password(self):
        r = self.c.post("/settings", data={"action": "password", "current": "wrong", "new": "newpass1",
                                           "confirm": "newpass1"}, follow_redirects=True)
        self.assertIn(b"Current password is incorrect", r.data)
        self.c.post("/settings", data={"action": "password", "current": "secret1", "new": "newpass1", "confirm": "newpass1"})
        self.c.post("/logout")
        r = self.c.post("/login", data={"username": "ali", "password": "newpass1"})
        self.assertEqual(r.status_code, 302)

    def test_404_page(self):
        r = self.c.get("/does-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertIn(b"Page not found", r.data)


# ------------------------------------------------------------------ transactions
class TransactionTests(Base):
    def test_add_income_and_expense_update_dashboard(self):
        self.add(type="income", category="Salary", amount=50000)
        self.add(type="expense", category="Food", amount=1234.5)
        html = self.c.get("/dashboard").data.decode()
        self.assertIn("Rs 48,766", html)       # balance 50000 - 1234.5 (rounded)
        self.assertIn("Rs 50,000", html)
        self.assertIn("Rs 1,234", html)

    def test_edit_and_delete(self):
        self.add(amount=500, description="Lunch")
        with app.app_context():
            tx_id = Transaction.query.first().id
        self.c.post(f"/transactions/{tx_id}/edit", data={"type": "expense", "category": "Transport",
                                                          "amount": "750", "date": "2026-01-05"})
        with app.app_context():
            t = db.session.get(Transaction, tx_id)
            self.assertEqual((t.amount, t.category, t.date), (750, "Transport", date(2026, 1, 5)))
        self.assertIn(b"Transport", self.c.get(f"/transactions/{tx_id}").data)
        self.c.post(f"/transactions/{tx_id}/delete")
        with app.app_context():
            self.assertIsNone(db.session.get(Transaction, tx_id))

    def test_invalid_input_rejected(self):
        for bad in [{"amount": "-5"}, {"amount": "abc"}, {"amount": "0"}, {"category": "Salary"},
                    {"type": "loan"}, {"date": "not-a-date"}, {"amount": "inf"}]:
            r = self.add(**bad)
            self.assertNotIn(b"added.", r.data, bad)
        with app.app_context():
            self.assertEqual(Transaction.query.count(), 0)

    def test_filters_search_and_sort(self):
        self.add(category="Food", amount=100, date="2026-01-10", description="Groceries")
        self.add(category="Transport", amount=900, date="2026-02-10", description="Fuel")
        self.add(type="income", category="Salary", amount=5000, date="2026-02-01", description="Pay")
        body = lambda url: self.c.get(url).data.decode()  # noqa: E731
        self.assertIn("Fuel", body("/transactions?category=Transport"))
        self.assertNotIn("Groceries", body("/transactions?category=Transport"))
        self.assertNotIn("Groceries", body("/transactions?start=2026-02-01"))
        self.assertIn("Groceries", body("/transactions?end=2026-01-31"))
        self.assertNotIn("Fuel", body("/transactions?type=income"))
        self.assertIn("Fuel", body("/transactions?min=500&max=1000"))
        self.assertNotIn("Groceries", body("/transactions?min=500&max=1000"))
        self.assertIn("Groceries", body("/transactions?q=grocer"))
        html = body("/transactions?sort=amount_asc")
        self.assertLess(html.index("Groceries"), html.index("Fuel"))
        html = body("/transactions?sort=amount_desc")
        self.assertLess(html.index("Pay"), html.index("Fuel"))

    def test_pagination(self):
        for i in range(25):
            self.add(amount=10 + i)
        html = self.c.get("/transactions").data.decode()
        self.assertIn("of 25", html)
        self.assertIn("Showing 21–25", self.c.get("/transactions?page=2").data.decode())


# ------------------------------------------------------------------ budgets & alerts
class BudgetTests(Base):
    def test_budget_crud_and_overrun(self):
        self.c.post("/budgets", data={"category": "Food", "amount": "1000", "scope": "recurring"})
        self.add(category="Food", amount=1500)
        html = self.c.get("/budgets").data.decode()
        self.assertIn("Over by Rs 500", html)
        self.assertIn("Food budget exceeded", html)
        with app.app_context():
            b = Budget.query.first()
            bid = b.id
        self.c.post(f"/budgets/{bid}/edit", data={"category": "Food", "amount": "2000", "scope": "recurring"})
        with app.app_context():
            self.assertEqual(db.session.get(Budget, bid).amount, 2000)
        self.assertNotIn("Over by", self.c.get("/budgets").data.decode())
        self.c.post(f"/budgets/{bid}/delete")
        with app.app_context():
            self.assertEqual(Budget.query.count(), 0)

    def test_month_budget_overrides_recurring(self):
        cur = current_month()
        self.c.post("/budgets", data={"category": "Food", "amount": "1000", "scope": "recurring"})
        self.c.post("/budgets", data={"category": "Food", "amount": "3000", "scope": "month", "month": cur})
        self.add(category="Food", amount=1500)
        with app.app_context():
            uid = self.uid()
            rows = budget_status(Transaction.query.filter_by(user_id=uid).all(),
                                 Budget.query.filter_by(user_id=uid).all(), cur)
        self.assertEqual(rows[0]["budget"], 3000)
        self.assertFalse(rows[0]["over"])
        # saving the same category+period again updates instead of duplicating
        self.c.post("/budgets", data={"category": "Food", "amount": "1200", "scope": "recurring"})
        with app.app_context():
            self.assertEqual(Budget.query.count(), 2)

    def test_invalid_budget_rejected(self):
        for bad in [{"category": "Salary", "amount": "100"}, {"category": "Food", "amount": "-1"},
                    {"category": "Food", "amount": "100", "scope": "month", "month": "2026-13"}]:
            self.c.post("/budgets", data={"scope": "recurring", **bad})
        with app.app_context():
            self.assertEqual(Budget.query.count(), 0)

    def test_overall_budget_and_category_spike_alerts(self):
        txs = [T(month_date(-i), 1000) for i in (1, 2, 3)] + [T(month_date(0), 2000)]
        alerts = build_alerts(txs, [], 1500, current_month())
        titles = [a["title"] for a in alerts]
        self.assertIn("Monthly budget exceeded", titles)
        self.assertIn("High Food spending", titles)
        self.assertEqual(build_alerts(txs[:3], [], 0, current_month()), [])


# ------------------------------------------------------------------ reports
class ReportTests(Base):
    def test_monthly_yearly_range_reports(self):
        self.add(type="income", category="Salary", amount=10000, date="2026-03-01")
        self.add(category="Food", amount=4000, date="2026-03-15")
        self.add(category="Rent", amount=1000, date="2025-12-15")
        y = self.c.get("/reports?mode=year&year=2026").data.decode()
        self.assertIn("Rs 10,000", y)
        self.assertIn("60.0%", y)               # savings rate (10000-4000)/10000
        self.assertNotIn("Rent", y.split("Category breakdown")[1].split("</table>")[0])
        m = self.c.get("/reports?mode=month&month=2026-03").data.decode()
        self.assertIn("March 2026", m)
        r = self.c.get("/reports?mode=range&start=2025-12-01&end=2026-03-31").data.decode()
        self.assertIn("Rs 5,000", r)            # 4000 + 1000 expenses
        self.assertEqual(self.c.get("/reports?mode=range&start=bad&end=bad").status_code, 200)


# ------------------------------------------------------------------ machine learning
class MLTests(unittest.TestCase):
    def series(self, values):
        n = len(values)
        return [T(month_date(-(n - i)), v) for i, v in enumerate(values)]

    def test_history_excludes_current_month_and_fills_gaps(self):
        txs = [T(month_date(-4), 100), T(month_date(-1), 300), T(month_date(0), 999)]
        hist = monthly_history(txs)
        self.assertEqual(list(hist.values()), [100, 0, 0, 300])
        self.assertNotIn(current_month(), hist)

    def test_insufficient_data(self):
        p = predict_next_month(self.series([1000, 1100]))
        self.assertFalse(p["ok"])
        self.assertIn("At least 3", p["reason"])

    def test_low_confidence_simple_forecast(self):
        p = predict_next_month(self.series([1000, 1200, 1400, 1600]))
        self.assertTrue(p["ok"])
        self.assertFalse(p["evaluated"])
        self.assertAlmostEqual(p["prediction"], 1400)   # mean of last 3
        self.assertIn("Low confidence", p["note"])
        self.assertEqual(p["target"], add_months(current_month(), 1))

    def test_evaluated_prediction_picks_best_model_on_trend(self):
        vals = [10000 + 500 * i for i in range(12)]     # perfectly linear trend
        p = predict_next_month(self.series(vals))
        self.assertTrue(p["evaluated"])
        self.assertEqual(p["model"], "Linear Regression")
        self.assertLess(p["metrics"]["Linear Regression"]["mae"], 1)
        # last completed month = index 11 (15500); target is 2 months ahead -> 16500
        self.assertAlmostEqual(p["prediction"], 16500, delta=1)
        self.assertEqual(set(p["metrics"]), {"Baseline (3-month average)", "Linear Regression", "Random Forest"})
        self.assertEqual(len(p["backtest"]["months"]), 4)

    def test_baseline_can_win_on_flat_noisy_data(self):
        vals = [20000, 20500, 19500, 20200, 19800, 20100, 19900, 20300, 19700, 20000, 20150, 19850]
        p = predict_next_month(self.series(vals))
        self.assertTrue(p["evaluated"])
        self.assertIn(p["model"], p["metrics"])
        best = min(p["metrics"].values(), key=lambda m: m["mae"])["mae"]
        self.assertEqual(p["metrics"][p["model"]]["mae"], best)

    def test_no_data_leakage_in_evaluation(self):
        """Changing a test-period value must not change the forecast made for it."""
        import numpy as np
        y = np.array([1000.0 + 50 * i for i in range(12)])
        months = [f"m{i}" for i in range(12)]
        r1, _, _ = evaluate(y, months)
        y2 = y.copy()
        y2[-1] = 99999   # actual of the last test month
        r2, _, _ = evaluate(y2, months)
        for name in r1:
            self.assertEqual(r1[name]["preds"], r2[name]["preds"])

    def test_predictions_never_negative(self):
        vals = [50000 - 4500 * i for i in range(11)] + [500]
        p = predict_next_month(self.series(vals))
        self.assertGreaterEqual(p["prediction"], 0)


class PredictionPageTests(Base):
    def test_prediction_page_and_history_log(self):
        self.c.post("/sample-data")
        html = self.c.get("/prediction").data.decode()
        self.assertIn("Evaluation metrics", html)
        self.assertIn("Random Forest", html)
        self.assertIn("sample demonstration data", html)
        with app.app_context():
            log = PredictionLog.query.first()
            self.assertEqual(log.target_month, add_months(current_month(), 1))
        self.assertIn("AI EXPENSE PREDICTION", self.c.get("/dashboard").data.decode())

    def test_empty_account_pages(self):
        for url in ["/dashboard", "/transactions", "/budgets", "/reports", "/prediction", "/data", "/settings"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)
        self.assertIn(b"Not enough data", self.c.get("/prediction").data)


# ------------------------------------------------------------------ sample data, export, backup
class DataTests(Base):
    def test_sample_data_labelled_and_removable(self):
        self.add(amount=777, description="My real entry")
        self.c.post("/sample-data")
        r = self.c.post("/sample-data", follow_redirects=True)
        self.assertIn(b"already loaded", r.data)
        with app.app_context():
            n = Transaction.query.count()
            self.assertGreater(n, 100)
            self.assertTrue(all(t.is_sample for t in Transaction.query if t.amount != 777))
        self.c.post("/sample-data/remove")
        with app.app_context():
            self.assertEqual([t.description for t in Transaction.query], ["My real entry"])

    def test_all_pages_with_sample_data(self):
        self.c.post("/sample-data")
        for url in ["/dashboard", f"/dashboard?month={add_months(current_month(), -3)}", "/transactions",
                    "/transactions?page=3", "/budgets", "/reports", "/reports?mode=month", "/reports?mode=range",
                    "/prediction", "/data", "/settings", "/dashboard?month=bad"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotIn(b"Traceback", r.data)

    def test_exports(self):
        self.add(amount=321, description="Export me")
        csv_data = self.c.get("/export/csv").data.decode("utf-8-sig")
        self.assertTrue(csv_data.startswith("Date,Type,Category,Amount (PKR),Description"))
        self.assertIn("Export me", csv_data)
        xl = self.c.get("/export/excel").data
        self.assertEqual(xl[:2], b"PK")
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(xl))
        self.assertEqual(wb.sheetnames, ["Transactions", "Monthly Summary", "Budgets"])
        self.assertEqual(wb["Transactions"]["D2"].value, 321)
        filtered = self.c.get("/export/csv?category=Transport").data.decode()
        self.assertNotIn("Export me", filtered)

    def test_backup_and_restore_round_trip(self):
        self.c.post("/sample-data")
        backup = self.c.get("/backup").data
        payload = json.loads(backup)
        n, nb = len(payload["transactions"]), len(payload["budgets"])
        self.assertGreater(nb, 0)
        r = self.c.post("/restore", data={"mode": "replace", "file": (io.BytesIO(backup), "b.json")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(f"Restored {n} transactions and {nb} budgets".encode(), r.data)
        with app.app_context():
            self.assertEqual(Transaction.query.count(), n)
            self.assertEqual(Budget.query.count(), nb)
        # merge mode adds
        self.c.post("/restore", data={"mode": "merge", "file": (io.BytesIO(backup), "b.json")},
                    content_type="multipart/form-data")
        with app.app_context():
            self.assertEqual(Transaction.query.count(), 2 * n)

    def test_invalid_backup_changes_nothing(self):
        self.add(amount=50)
        bad_files = [b"not json", b'{"app": "other"}',
                     json.dumps({"app": "finance-tracker", "transactions": [
                         {"type": "expense", "category": "Food", "amount": 10, "date": "2026-01-01"},
                         {"type": "expense", "category": "Food", "amount": -10, "date": "2026-01-01"}]}).encode()]
        for raw in bad_files:
            r = self.c.post("/restore", data={"mode": "replace", "file": (io.BytesIO(raw), "x.json")},
                            content_type="multipart/form-data", follow_redirects=True)
            self.assertIn(b"Your data was not changed", r.data)
        with app.app_context():
            self.assertEqual(Transaction.query.count(), 1)

    def test_v1_backup_still_restores(self):
        raw = json.dumps({"app": "finance-tracker", "version": 1, "monthly_budget": 5000, "transactions": [
            {"type": "income", "category": "Salary", "amount": 100, "date": "2026-01-01", "description": "old"}]}).encode()
        r = self.c.post("/restore", data={"mode": "merge", "file": (io.BytesIO(raw), "v1.json")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Restored 1 transactions", r.data)

    def test_health(self):
        self.assertEqual(self.c.get("/health").json["status"], "ok")


# ------------------------------------------------------------------ frontend assets & 3D data
class DashboardPredictionTests(Base):
    def test_dashboard_displays_next_month_prediction(self):
        for i in range(1, 11):
            self.add(amount=20000 + i * 500, date=month_date(-i).isoformat())
        html = self.c.get("/dashboard").data.decode()
        with app.app_context():
            expected = predict_next_month(Transaction.query.filter_by(user_id=self.uid()).all())
        self.assertTrue(expected["ok"] and expected["evaluated"])
        data = json.loads(re.search(r"window\.DASH = (\{.*?\});</script>", html).group(1))
        self.assertEqual(data["chart"]["forecast"]["value"], expected["prediction"])
        self.assertIn(f"Rs {expected['prediction']:,.0f}", html)
        self.assertIn(expected["model"], html)


class ReviewFixTests(Base):
    """Regression tests for issues found in the final code review."""
    def test_export_blocks_spreadsheet_formula_injection(self):
        self.add(description="=HYPERLINK(\"http://evil\",\"x\")")
        csv_text = self.c.get("/export/csv").get_data(as_text=True)
        self.assertIn("'=HYPERLINK", csv_text)
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(self.c.get("/export/excel").data))
        cell = wb["Transactions"]["E2"]
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(cell.value.startswith("'="))

    def test_out_of_range_report_year_does_not_crash(self):
        for y in ("0", "99999", "-5"):
            self.assertEqual(self.c.get(f"/reports?mode=year&year={y}").status_code, 200)

    def test_prediction_refreshes_when_a_transaction_date_changes(self):
        for i in range(1, 10):
            self.add(amount=10000 + i * 1000, date=month_date(-i).isoformat())
        before = self.c.get("/prediction").get_data(as_text=True)
        with app.app_context():
            tx = Transaction.query.filter_by(amount=19000.0).first()
            tx.date = month_date(-1)          # same amount and count, different month
            db.session.commit()
        after = self.c.get("/prediction").get_data(as_text=True)
        self.assertNotEqual(re.findall(r"Rs [0-9,]+", before)[:3], re.findall(r"Rs [0-9,]+", after)[:3])


class FrontendTests(Base):
    ASSETS = ["vendor/bootstrap.min.css", "vendor/bootstrap.bundle.min.js", "vendor/gsap.min.js",
              "vendor/plotly-basic.min.js", "vendor/three.module.min.js", "vendor/bootstrap-icons/bootstrap-icons.min.css",
              "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2", "vendor/fonts/inter-var.woff2",
              "vendor/fonts/space-grotesk-var.woff2", "js/fin3d.js", "app.js", "style.css"]

    def test_all_assets_served_locally_no_cdn(self):
        for a in self.ASSETS:
            r = self.c.get("/static/" + a)
            self.assertEqual(r.status_code, 200, a)
            r.close()
        self.c.post("/sample-data")
        for url in ["/login", "/dashboard", "/prediction", "/reports", "/transactions"]:
            html = self.c.get(url).data.decode()
            self.assertNotIn("cdn.jsdelivr", html, url)
            self.assertNotIn("fonts.googleapis", html, url)

    def test_dashboard_3d_chart_uses_database_values(self):
        self.add(type="income", category="Salary", amount=50000)
        self.add(type="expense", category="Food", amount=12000)
        html = self.c.get("/dashboard").data.decode()
        self.assertIn("js/fin3d.js", html)
        data = json.loads(re.search(r"window\.DASH = (\{.*?\});</script>", html).group(1))
        self.assertEqual(data["chart"]["income"][-1], 50000)
        self.assertEqual(data["chart"]["expense"][-1], 12000)
        self.assertEqual(data["chart"]["savings"][-1], 38000)
        self.assertEqual(data["chart"]["balance"][-1], 38000)
        self.assertEqual(data["cats"], {"Food": 12000})

    def test_login_page_has_3d_scene_and_fallback_safe(self):
        html = self.c.get("/logout", follow_redirects=True).data.decode()
        self.assertIn('id="loginScene"', html)
        self.assertIn("webglAvailable()", html)


if __name__ == "__main__":
    unittest.main()
