from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

CATEGORIES = {
    "expense": ["Food", "Transport", "Bills", "Shopping", "Rent", "Health",
                "Education", "Entertainment", "Other"],
    "income": ["Salary", "Freelance", "Business", "Gift", "Investment", "Other"],
}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    monthly_budget = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    transactions = db.relationship("Transaction", backref="user", lazy=True,
                                   cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # "income" or "expense"
    category = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(200), default="")

    def to_dict(self):
        return {
            "type": self.type,
            "category": self.category,
            "amount": self.amount,
            "date": self.date.isoformat(),
            "description": self.description or "",
        }
