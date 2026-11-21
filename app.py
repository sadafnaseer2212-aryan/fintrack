import csv
import io
import json
import os
import random
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from openpyxl import Workbook
from openpyxl.styles import Font

from ml import month_key, monthly_expense_totals, predict_next_month
from models import CATEGORIES, Transaction, User, db

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
db_path = os.environ.get("DATABASE_PATH",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "instance", "finance.db"))
os.makedirs(os.path.dirname(db_path), exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db_path
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

with app.app_context():
    try:
        db.create_all()
    except Exception:  # another process created the tables at the same moment
        db.session.rollback()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_globals():
    return {"CATEGORIES": CATEGORIES, "today": date.today()}


def user_tx():
    return Transaction.query.filter_by(user_id=current_user.id)


def parse_date(value, default=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- auth
@app.route("/")
def index():
    return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not username or not email or len(password) < 6:
            flash("All fields are required and password must be at least 6 characters.", "danger")
        elif password != request.form.get("confirm", ""):
            flash("Passwords do not match.", "danger")
        elif User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or email already exists.", "danger")
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Account created. Welcome!", "success")
            return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ident = request.form.get("username", "").strip()
        user = User.query.filter((User.username == ident) | (User.email == ident.lower())).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------- dashboard
def overspending(transactions, budget):
    """Return warnings for categories spending well above their usual level."""
    cur = month_key(date.today())
    by_cat_month = defaultdict(lambda: defaultdict(float))
    for t in transactions:
        if t.type == "expense":
            by_cat_month[t.category][month_key(t.date)] += t.amount
    alerts = []
    for cat, months in by_cat_month.items():
        past = [v for k, v in months.items() if k < cur]
        now = months.get(cur, 0)
        if past and now > 0:
            avg = sum(past) / len(past)
            if avg > 0 and now > avg * 1.2:
                alerts.append(f"{cat}: {now:,.0f} this month vs. usual {avg:,.0f} "
                              f"(+{(now / avg - 1) * 100:.0f}%)")
    month_spend = sum(m.get(cur, 0) for m in by_cat_month.values())
    if budget and month_spend > budget:
        alerts.insert(0, f"Monthly budget exceeded: spent {month_spend:,.0f} of {budget:,.0f}")
    return alerts


@app.route("/dashboard")
@login_required
def dashboard():
    txs = user_tx().all()
    income = sum(t.amount for t in txs if t.type == "income")
    expense = sum(t.amount for t in txs if t.type == "expense")
    cur = month_key(date.today())
    m_income = sum(t.amount for t in txs if t.type == "income" and month_key(t.date) == cur)
    m_expense = sum(t.amount for t in txs if t.type == "expense" and month_key(t.date) == cur)

    totals = monthly_expense_totals(txs)
    inc_by_month = defaultdict(float)
    for t in txs:
        if t.type == "income":
            inc_by_month[month_key(t.date)] += t.amount
    months = sorted(set(totals) | set(inc_by_month))[-12:]
    cat_totals = defaultdict(float)
    for t in txs:
        if t.type == "expense" and month_key(t.date) == cur:
            cat_totals[t.category] += t.amount

    prediction = predict_next_month(txs)
    chart = {
        "months": months,
        "expense": [round(totals.get(m, 0), 2) for m in months],
        "income": [round(inc_by_month.get(m, 0), 2) for m in months],
        "cat_labels": list(cat_totals), "cat_values": [round(v, 2) for v in cat_totals.values()],
        "prediction": prediction,
    }
    recent = user_tx().order_by(Transaction.date.desc(), Transaction.id.desc()).limit(6).all()
    return render_template("dashboard.html", income=income, expense=expense,
                           balance=income - expense, m_income=m_income, m_expense=m_expense,
                           prediction=prediction, recent=recent, chart=chart,
                           alerts=overspending(txs, current_user.monthly_budget))


# ---------------------------------------------------------------- transactions
def filtered_query():
    q = user_tx()
    a = request.args
    if a.get("type") in ("income", "expense"):
        q = q.filter_by(type=a["type"])
    if a.get("category"):
        q = q.filter_by(category=a["category"])
    if parse_date(a.get("start")):
        q = q.filter(Transaction.date >= parse_date(a["start"]))
    if parse_date(a.get("end")):
        q = q.filter(Transaction.date <= parse_date(a["end"]))
    try:
        if a.get("min"):
            q = q.filter(Transaction.amount >= float(a["min"]))
        if a.get("max"):
            q = q.filter(Transaction.amount <= float(a["max"]))
    except ValueError:
        flash("Amount filter must be a number.", "warning")
    if a.get("q"):
        q = q.filter(Transaction.description.ilike(f"%{a['q']}%"))
    return q.order_by(Transaction.date.desc(), Transaction.id.desc())


@app.route("/transactions")
@login_required
def transactions():
    items = filtered_query().all()
    total_in = sum(t.amount for t in items if t.type == "income")
    total_out = sum(t.amount for t in items if t.type == "expense")
    all_cats = sorted(set(CATEGORIES["income"] + CATEGORIES["expense"]))
    return render_template("transactions.html", items=items, total_in=total_in,
                           total_out=total_out, all_cats=all_cats, args=request.args)


def save_tx_from_form(tx):
    tx_type = request.form.get("type")
    category = request.form.get("category")
    try:
        amount = float(request.form.get("amount", ""))
    except ValueError:
        amount = -1
    if tx_type not in CATEGORIES or category not in CATEGORIES[tx_type] or amount <= 0:
        flash("Please choose a valid type, category and a positive amount.", "danger")
        return False
    tx.type, tx.category, tx.amount = tx_type, category, round(amount, 2)
    tx.date = parse_date(request.form.get("date"), date.today())
    tx.description = request.form.get("description", "").strip()[:200]
    return True


@app.route("/transactions/new", methods=["GET", "POST"])
@login_required
def add_transaction():
    tx = Transaction(user_id=current_user.id, type=request.args.get("type", "expense"),
                     date=date.today())
    if request.method == "POST" and save_tx_from_form(tx):
        db.session.add(tx)
        db.session.commit()
        flash("Entry added.", "success")
        return redirect(url_for("transactions"))
    return render_template("transaction_form.html", tx=tx, title="Add Entry")


@app.route("/transactions/<int:tx_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id):
    tx = user_tx().filter_by(id=tx_id).first() or abort(404)
    if request.method == "POST" and save_tx_from_form(tx):
        db.session.commit()
        flash("Entry updated.", "success")
        return redirect(url_for("transactions"))
    return render_template("transaction_form.html", tx=tx, title="Edit Entry")


@app.route("/transactions/<int:tx_id>/delete", methods=["POST"])
@login_required
def delete_transaction(tx_id):
    tx = user_tx().filter_by(id=tx_id).first() or abort(404)
    db.session.delete(tx)
    db.session.commit()
    flash("Entry deleted.", "info")
    return redirect(request.referrer or url_for("transactions"))


# ---------------------------------------------------------------- reports
@app.route("/reports")
@login_required
def reports():
    txs = user_tx().all()
    years = sorted({t.date.year for t in txs} | {date.today().year}, reverse=True)
    year = request.args.get("year", type=int) or date.today().year
    month = request.args.get("month", type=int) or 0  # 0 = whole year

    sel = [t for t in txs if t.date.year == year and (not month or t.date.month == month)]
    by_cat = defaultdict(float)
    for t in sel:
        if t.type == "expense":
            by_cat[t.category] += t.amount
    by_cat = dict(sorted(by_cat.items(), key=lambda kv: -kv[1]))

    monthly = []
    for m in range(1, 13):
        inc = sum(t.amount for t in txs if t.type == "income" and t.date.year == year and t.date.month == m)
        exp = sum(t.amount for t in txs if t.type == "expense" and t.date.year == year and t.date.month == m)
        monthly.append({"month": date(year, m, 1).strftime("%b"), "income": round(inc, 2),
                        "expense": round(exp, 2), "net": round(inc - exp, 2)})

    income = sum(t.amount for t in sel if t.type == "income")
    expense = sum(t.amount for t in sel if t.type == "expense")
    exp_months = [r["expense"] for r in monthly if r["expense"] > 0]
    avg_exp = sum(exp_months) / len(exp_months) if exp_months else 0
    for r in monthly:
        r["over"] = avg_exp > 0 and r["expense"] > avg_exp * 1.2
    return render_template("reports.html", years=years, year=year, month=month,
                           by_cat=by_cat, monthly=monthly, income=income, expense=expense,
                           avg_exp=avg_exp, alerts=overspending(txs, current_user.monthly_budget))


# ---------------------------------------------------------------- export / backup
@app.route("/export/csv")
@login_required
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Type", "Category", "Amount", "Description"])
    for t in filtered_query().all():
        w.writerow([t.date.isoformat(), t.type, t.category, t.amount, t.description])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=transactions.csv"})


@app.route("/export/excel")
@login_required
def export_excel():
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["Date", "Type", "Category", "Amount", "Description"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for t in filtered_query().all():
        ws.append([t.date, t.type, t.category, t.amount, t.description])
    for col, width in zip("ABCDE", (12, 10, 15, 12, 40)):
        ws.column_dimensions[col].width = width
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="transactions.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/backup")
@login_required
def backup():
    data = {"app": "finance-tracker", "version": 1, "exported_at": datetime.utcnow().isoformat(),
            "monthly_budget": current_user.monthly_budget,
            "transactions": [t.to_dict() for t in user_tx().order_by(Transaction.date).all()]}
    name = f"finance-backup-{date.today().isoformat()}.json"
    return Response(json.dumps(data, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@app.route("/restore", methods=["POST"])
@login_required
def restore():
    f = request.files.get("file")
    try:
        data = json.load(f)
        rows = data["transactions"]
        new = []
        for r in rows:
            if r["type"] not in CATEGORIES or float(r["amount"]) <= 0:
                raise ValueError("bad row")
            new.append(Transaction(user_id=current_user.id, type=r["type"],
                                   category=str(r["category"])[:40], amount=float(r["amount"]),
                                   date=parse_date(r["date"]) or date.today(),
                                   description=str(r.get("description", ""))[:200]))
    except Exception:
        flash("Invalid backup file.", "danger")
        return redirect(url_for("settings"))
    if request.form.get("mode") == "replace":
        user_tx().delete()
    db.session.add_all(new)
    if data.get("monthly_budget") is not None:
        current_user.monthly_budget = float(data["monthly_budget"])
    db.session.commit()
    flash(f"Restored {len(new)} entries.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- settings
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        try:
            current_user.monthly_budget = max(0.0, float(request.form.get("budget") or 0))
            db.session.commit()
            flash("Budget saved.", "success")
        except ValueError:
            flash("Budget must be a number.", "danger")
        return redirect(url_for("settings"))
    return render_template("settings.html", count=user_tx().count())


@app.route("/sample-data", methods=["POST"])
@login_required
def sample_data():
    """Add 12 months of realistic demo data so charts and prediction can be shown."""
    rnd = random.Random(current_user.id)
    base = {"Food": 18000, "Transport": 7000, "Bills": 9000, "Shopping": 6000,
            "Rent": 30000, "Health": 2500, "Entertainment": 3000}
    start = date.today().replace(day=1)
    rows = []
    for i in range(12, -1, -1):
        y, m = divmod(start.year * 12 + start.month - 1 - i, 12)
        first = date(y, m + 1, 1)
        growth = 1 + (12 - i) * 0.015
        rows.append(Transaction(user_id=current_user.id, type="income", category="Salary",
                                amount=120000, date=first, description="Monthly salary"))
        if rnd.random() < 0.5:
            rows.append(Transaction(user_id=current_user.id, type="income", category="Freelance",
                                    amount=round(rnd.uniform(10000, 30000), -2),
                                    date=first + timedelta(days=14), description="Freelance project"))
        last_day = date.today().day if i == 0 else 28
        for cat, amt in base.items():
            for _ in range(1 if cat in ("Rent", "Bills") else 3):
                rows.append(Transaction(
                    user_id=current_user.id, type="expense", category=cat,
                    amount=round(amt * growth * rnd.uniform(0.25, 0.45) * (3 if cat in ("Rent", "Bills") else 1), -1),
                    date=first + timedelta(days=rnd.randint(0, last_day - 1)),
                    description=f"{cat} expense"))
    db.session.add_all(rows)
    if not current_user.monthly_budget:
        current_user.monthly_budget = 80000
    db.session.commit()
    flash(f"Added {len(rows)} sample entries.", "success")
    return redirect(url_for("dashboard"))


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
