from datetime import date, datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

CATEGORIES = {
    "expense": ["Food", "Transport", "Bills", "Shopping", "Rent", "Health",
                "Education", "Entertainment", "Other"],
    "income": ["Salary", "Freelance", "Business", "Gift", "Investment", "Other"],
}

# Descriptions of generated demonstration entries start with this marker so
# they can always be identified (and removed) and are never mistaken for real data.
SAMPLE_PREFIX = "Sample · "


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    monthly_budget = db.Column(db.Float, default=0)  # overall monthly spending limit
    created_at = db.Column(db.DateTime, default=utcnow)
    transactions = db.relationship("Transaction", backref="user", lazy=True,
                                   cascade="all, delete-orphan")
    budgets = db.relationship("Budget", backref="user", lazy=True,
                              cascade="all, delete-orphan")
    predictions = db.relationship("PredictionLog", backref="user", lazy=True,
                                  cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    type = db.Column(db.String(10), nullable=False)  # "income" or "expense"
    category = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(200), default="")

    @property
    def is_sample(self):
        return (self.description or "").startswith(SAMPLE_PREFIX)

    def to_dict(self):
        return {"type": self.type, "category": self.category, "amount": self.amount,
                "date": self.date.isoformat(), "description": self.description or ""}


class Budget(db.Model):
    """Spending limit for one expense category.

    month = "YYYY-MM" applies to that month only; month = NULL applies to every
    month (a month-specific budget overrides the recurring one).
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    category = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    month = db.Column(db.String(7), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {"category": self.category, "amount": self.amount, "month": self.month}


class PredictionLog(db.Model):
    """The forecast made for a target month, kept so it can later be compared
    with the actual spending once that month is complete."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    target_month = db.Column(db.String(7), nullable=False)
    model_name = db.Column(db.String(40), nullable=False)
    predicted = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "target_month"),)
