"""Automated tests. Run with:  python -m unittest discover tests"""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from app import app  # noqa: E402
from ml import next_month, predict_next_month  # noqa: E402
from models import Transaction, db  # noqa: E402


class FinanceTrackerTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.c = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
        self.c.post("/register", data={"username": "ali", "email": "ali@test.com",
                                       "password": "secret1", "confirm": "secret1"})

    def test_auth_required(self):
        self.c.get("/logout")
        r = self.c.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_bad_login(self):
        self.c.get("/logout")
        r = self.c.post("/login", data={"username": "ali", "password": "wrong"})
        self.assertIn(b"Invalid username or password", r.data)

    def test_crud_and_filter(self):
        today = date.today().isoformat()
        self.c.post("/transactions/new", data={"type": "expense", "category": "Food",
                                               "amount": "500", "date": today, "description": "Lunch"})
        self.c.post("/transactions/new", data={"type": "income", "category": "Salary",
                                               "amount": "1000", "date": today})
        r = self.c.get("/transactions?type=expense")
        self.assertIn(b"Lunch", r.data)
        self.assertNotIn(b"Salary</td>", r.data)
        with app.app_context():
            tx = Transaction.query.filter_by(category="Food").first()
            tx_id = tx.id
        self.c.post(f"/transactions/{tx_id}/edit", data={"type": "expense", "category": "Food",
                                                         "amount": "750", "date": today})
        with app.app_context():
            self.assertEqual(db.session.get(Transaction, tx_id).amount, 750)
        self.c.post(f"/transactions/{tx_id}/delete")
        with app.app_context():
            self.assertIsNone(db.session.get(Transaction, tx_id))

    def test_invalid_amount_rejected(self):
        r = self.c.post("/transactions/new", data={"type": "expense", "category": "Food", "amount": "-5"})
        self.assertIn(b"positive amount", r.data)

    def test_pages_with_sample_data(self):
        self.c.post("/sample-data")
        for url in ["/dashboard", "/transactions", "/reports", "/settings"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)
        self.assertIn(b"AI Expense Prediction", self.c.get("/dashboard").data)

    def test_export_backup_restore(self):
        self.c.post("/sample-data")
        self.assertIn(b"Date,Type,Category", self.c.get("/export/csv").data)
        self.assertEqual(self.c.get("/export/excel").data[:2], b"PK")  # xlsx is a zip
        backup = self.c.get("/backup").data
        n = len(json.loads(backup)["transactions"])
        r = self.c.post("/restore", data={"mode": "replace", "file": (io.BytesIO(backup), "b.json")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(f"Restored {n} entries".encode(), r.data)

    def test_prediction_trend(self):
        class T:  # minimal transaction stand-in
            def __init__(self, d, a):
                self.type, self.date, self.amount = "expense", d, a
        cur = date.today()
        months, k = [], f"{cur.year}-{cur.month:02d}"
        for _ in range(6):  # 6 past months, going backwards
            y, m = map(int, k.split("-"))
            k = f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
            months.insert(0, k)
        txs = [T(date(int(mk[:4]), int(mk[5:]), 1), 1000 + 100 * i) for i, mk in enumerate(months)]
        p = predict_next_month(txs)
        self.assertTrue(p["ok"])
        self.assertEqual(p["trend"], "up")
        self.assertEqual(p["month"], next_month(f"{cur.year}-{cur.month:02d}"))
        self.assertAlmostEqual(p["models"]["Linear Regression"], 1700, delta=1)

    def test_health(self):
        self.assertEqual(self.c.get("/health").json["status"], "ok")


if __name__ == "__main__":
    unittest.main()
