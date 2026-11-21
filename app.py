"""FinTrack: Personal Finance Tracker with Expense Prediction (Flask application)."""
import csv
import io
import json
import math
import os
import random
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_file, session, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func

from finance import (add_months, budget_status, build_alerts, category_totals,
                     current_month, effective_budgets, mkey, month_label,
                     month_range, pct_change, totals_by_month, valid_month)
from ml import predict_next_month
from models import (CATEGORIES, SAMPLE_PREFIX, Budget, PredictionLog,
                    Transaction, User, db, utcnow)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SECRET = "dev-secret-change-me"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or DEFAULT_SECRET,
    SQLALCHEMY_DATABASE_URI="sqlite:///" + (os.environ.get("DATABASE_PATH")
                                            or os.path.join(BASE_DIR, "instance", "finance.db")),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,      # backup upload limit
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_HTTPONLY=True,
    # set SESSION_COOKIE_SECURE=1 when the site is served over HTTPS (e.g. on Render)
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    REMEMBER_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    CSRF_ENABLED=True,
)
os.makedirs(os.path.dirname(app.config["SQLALCHEMY_DATABASE_URI"][len("sqlite:///"):]), exist_ok=True)
if app.config["SECRET_KEY"] == DEFAULT_SECRET:
    app.logger.info("SECRET_KEY not set: using a development key (fine locally; set SECRET_KEY in production).")

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

with app.app_context():
    try:
        db.create_all()  # additive: creates missing tables, never alters/drops existing ones
    except Exception:    # another worker created the tables at the same moment
        db.session.rollback()

PER_PAGE = 20
SORTS = {
    "date_desc": (Transaction.date.desc(), Transaction.id.desc()),
    "date_asc": (Transaction.date.asc(), Transaction.id.asc()),
    "amount_desc": (Transaction.amount.desc(),),
    "amount_asc": (Transaction.amount.asc(),),
    "category": (Transaction.category.asc(), Transaction.date.desc()),
}


# ================================================================== security
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(24)
    return session["_csrf"]


@app.before_request
def csrf_protect():
    if request.method == "POST" and app.config["CSRF_ENABLED"]:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
        if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
            abort(400, description="Your session expired or the form was invalid. Please try again.")


def safe_next(target):
    """Only allow redirects to paths on this site (prevents open redirects)."""
    if target and target.startswith("/") and not target.startswith("//") \
            and not urlparse(target).netloc:
        return target
    return url_for("dashboard")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ================================================================== template helpers
@app.template_filter("pkr")
def pkr(value, decimals=0):
    value = value or 0
    sign = "-" if value < 0 else ""
    return f"{sign}Rs {abs(value):,.{decimals}f}"


@app.template_filter("fdate")
def fdate(d):
    return d.strftime("%d %b %Y") if d else ""


@app.template_filter("mlabel")
def mlabel(key, fmt="%b %Y"):
    return month_label(key, fmt) if key else ""


@app.context_processor
def inject_globals():
    ctx = {"CATEGORIES": CATEGORIES, "today": date.today(), "csrf_token": csrf_token,
           "nav_alerts": []}
    if current_user.is_authenticated and request.endpoint not in ("static", None):
        txs = user_tx().all()
        ctx["nav_alerts"] = build_alerts(txs, current_user.budgets,
                                         current_user.monthly_budget, current_month())
    return ctx


def user_tx():
    return Transaction.query.filter_by(user_id=current_user.id)


def parse_date(value, default=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def parse_amount(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if math.isfinite(v) and 0 < v < 1e10 else None


def prediction_for(txs):
    """Cached prediction (re-computed only when the user's data changes) + history log."""
    # The key covers every field the model uses, so editing a date, type or amount refreshes it.
    key = (current_user.id, current_month(),
           hash(tuple(sorted((t.id, t.type, t.date.toordinal(), round(t.amount, 2)) for t in txs))))
    pred = predict_next_month(txs, use_cache_key=key)
    if pred.get("ok"):
        log = PredictionLog.query.filter_by(user_id=current_user.id,
                                            target_month=pred["target"]).first()
        if not log:
            db.session.add(PredictionLog(user_id=current_user.id, target_month=pred["target"],
                                         model_name=pred["model"], predicted=pred["prediction"]))
            db.session.commit()
        elif abs(log.predicted - pred["prediction"]) > 0.005 or log.model_name != pred["model"]:
            log.predicted, log.model_name = pred["prediction"], pred["model"]
            db.session.commit()
    return pred


def has_sample_data():
    return user_tx().filter(Transaction.description.like(SAMPLE_PREFIX + "%")).first() is not None


# ================================================================== auth
@app.route("/")
def index():
    return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not (3 <= len(username) <= 40) or not username.replace("_", "").isalnum():
            flash("Username must be 3–40 letters, numbers or underscores.", "danger")
        elif "@" not in email or "." not in email.split("@")[-1] or len(email) > 120:
            flash("Please enter a valid email address.", "danger")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
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
            flash("Account created. Welcome to FinTrack!", "success")
            return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        ident = request.form.get("username", "").strip()
        user = User.query.filter((User.username == ident) | (User.email == ident.lower())).first()
        if user and user.check_password(request.form.get("password", "")):
            session.clear()
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(safe_next(request.args.get("next")))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ================================================================== dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    month = request.args.get("month", "")
    month = month if valid_month(month) else current_month()
    prev = add_months(month, -1)
    txs = user_tx().all()
    budgets = current_user.budgets

    inc_m, exp_m = totals_by_month(txs, "income"), totals_by_month(txs, "expense")
    upto = lambda d, k: sum(v for m, v in d.items() if m <= k)  # noqa: E731
    balance, balance_prev = upto(inc_m, month) - upto(exp_m, month), upto(inc_m, prev) - upto(exp_m, prev)
    avg3 = sum(exp_m.get(add_months(month, -i), 0) for i in (1, 2, 3)) / 3
    stats = {
        "balance": balance, "balance_change": pct_change(balance, balance_prev) if balance_prev > 0 else None,
        "income": upto(inc_m, month), "income_month": inc_m.get(month, 0),
        "income_change": pct_change(inc_m.get(month, 0), inc_m.get(prev, 0)),
        "expense": upto(exp_m, month), "expense_month": exp_m.get(month, 0),
        "expense_change": pct_change(exp_m.get(month, 0), exp_m.get(prev, 0)),
        "avg3": avg3, "spend_vs_avg": pct_change(exp_m.get(month, 0), avg3),
        "budget_used": (exp_m.get(month, 0) / current_user.monthly_budget * 100)
        if current_user.monthly_budget else None,
    }

    months = month_range(add_months(month, -11), month)
    is_current = month == current_month()
    prediction = prediction_for(txs)
    cats = category_totals(txs, month)
    chart = {
        "months": [month_label(m, "%b %y") for m in months],
        "income": [round(inc_m.get(m, 0), 2) for m in months],
        "expense": [round(exp_m.get(m, 0), 2) for m in months],
        "savings": [round(inc_m.get(m, 0) - exp_m.get(m, 0), 2) for m in months],
        "balance": [round(upto(inc_m, m) - upto(exp_m, m), 2) for m in months],
        "cat_labels": list(cats), "cat_values": [round(v, 2) for v in cats.values()],
        "forecast": {"label": month_label(prediction["target"], "%b %y"), "value": prediction["prediction"]}
        if is_current and prediction.get("ok") else None,
    }
    data_months = sorted({mkey(t.date) for t in txs} | {current_month()}, reverse=True)
    recent = user_tx().order_by(Transaction.date.desc(), Transaction.id.desc()).limit(6).all()
    return render_template(
        "dashboard.html", month=month, stats=stats, chart=chart, cats=cats, recent=recent,
        prediction=prediction, is_current=is_current, months_options=data_months[:24],
        budgets=budget_status(txs, budgets, month), has_tx=bool(txs),
        alerts=build_alerts(txs, budgets, current_user.monthly_budget, month,
                            prediction if is_current else None),
        sample=has_sample_data())


# ================================================================== transactions
def filtered_query(args):
    q = user_tx()
    if args.get("type") in ("income", "expense"):
        q = q.filter_by(type=args["type"])
    if args.get("category"):
        q = q.filter_by(category=args["category"])
    if parse_date(args.get("start")):
        q = q.filter(Transaction.date >= parse_date(args["start"]))
    if parse_date(args.get("end")):
        q = q.filter(Transaction.date <= parse_date(args["end"]))
    for key, op in (("min", "__ge__"), ("max", "__le__")):
        if args.get(key):
            try:
                q = q.filter(getattr(Transaction.amount, op)(float(args[key])))
            except ValueError:
                flash(f"'{args[key]}' is not a valid amount.", "warning")
    if args.get("q"):
        term = f"%{args['q'].strip()}%"
        q = q.filter(Transaction.description.ilike(term) | Transaction.category.ilike(term))
    return q.order_by(*SORTS.get(args.get("sort"), SORTS["date_desc"]))


@app.route("/transactions")
@login_required
def transactions():
    q = filtered_query(request.args)
    sums = dict(q.with_entities(Transaction.type, func.sum(Transaction.amount))
                .group_by(Transaction.type).order_by(None).all())
    page = q.paginate(page=request.args.get("page", 1, type=int), per_page=PER_PAGE, error_out=False)
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    all_cats = sorted(set(CATEGORIES["income"] + CATEGORIES["expense"]))
    return render_template("transactions.html", page=page, args=args, all_cats=all_cats,
                           total_in=sums.get("income", 0) or 0, total_out=sums.get("expense", 0) or 0,
                           filtered=bool(set(args) - {"sort"}))


def save_tx_from_form(tx):
    tx_type = request.form.get("type")
    category = request.form.get("category")
    amount = parse_amount(request.form.get("amount"))
    tx_date = parse_date(request.form.get("date"))
    errors = []
    if tx_type not in CATEGORIES:
        errors.append("Choose income or expense.")
    elif category not in CATEGORIES[tx_type]:
        errors.append("Choose a valid category.")
    if amount is None:
        errors.append("Enter a positive amount.")
    if tx_date is None:
        errors.append("Enter a valid date.")
    elif tx_date > date.today() + timedelta(days=366):
        errors.append("Date cannot be more than a year in the future.")
    if errors:
        for e in errors:
            flash(e, "danger")
        # keep what the user typed
        tx.type, tx.category = tx_type or tx.type, category
        tx.description = request.form.get("description", "")[:200]
        return False
    tx.type, tx.category, tx.amount, tx.date = tx_type, category, amount, tx_date
    tx.description = request.form.get("description", "").strip()[:200]
    return True


def form_side_context():
    """This month's totals and the latest entries, shown beside the add/edit form."""
    cur = current_month()
    month_tx = [t for t in user_tx().all() if mkey(t.date) == cur]
    return {"side_income": sum(t.amount for t in month_tx if t.type == "income"),
            "side_expense": sum(t.amount for t in month_tx if t.type == "expense"),
            "side_count": len(month_tx),
            "side_recent": user_tx().order_by(Transaction.date.desc(), Transaction.id.desc()).limit(5).all()}


def own_tx(tx_id):
    return user_tx().filter_by(id=tx_id).first() or abort(404)


@app.route("/transactions/new", methods=["GET", "POST"])
@login_required
def add_transaction():
    tx = Transaction(user_id=current_user.id, type=request.args.get("type", "expense"),
                     date=date.today())
    if tx.type not in CATEGORIES:
        tx.type = "expense"
    if request.method == "POST" and save_tx_from_form(tx):
        db.session.add(tx)
        db.session.commit()
        flash(f"{tx.type.title()} of {pkr(tx.amount)} added.", "success")
        if request.form.get("another"):
            return redirect(url_for("add_transaction", type=tx.type))
        return redirect(url_for("transactions"))
    return render_template("transaction_form.html", tx=tx, title="Add Transaction", **form_side_context())


@app.route("/transactions/<int:tx_id>")
@login_required
def transaction_detail(tx_id):
    tx = own_tx(tx_id)
    same_cat = (user_tx().filter_by(type=tx.type, category=tx.category)
                .with_entities(func.count(), func.sum(Transaction.amount), func.avg(Transaction.amount)).one())
    return render_template("transaction_detail.html", tx=tx, cat_count=same_cat[0],
                           cat_total=same_cat[1] or 0, cat_avg=same_cat[2] or 0)


@app.route("/transactions/<int:tx_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id):
    tx = own_tx(tx_id)
    if request.method == "POST":
        if save_tx_from_form(tx):
            db.session.commit()
            flash("Transaction updated.", "success")
            return redirect(url_for("transactions"))
        db.session.rollback()
        tx = own_tx(tx_id)
    return render_template("transaction_form.html", tx=tx, title="Edit Transaction", **form_side_context())


@app.route("/transactions/<int:tx_id>/delete", methods=["POST"])
@login_required
def delete_transaction(tx_id):
    tx = own_tx(tx_id)
    db.session.delete(tx)
    db.session.commit()
    flash("Transaction deleted.", "info")
    return redirect(safe_next(request.form.get("next")) if request.form.get("next")
                    else url_for("transactions"))


# ================================================================== budgets
@app.route("/budgets", methods=["GET", "POST"])
@login_required
def budgets():
    month = request.args.get("month", "")
    month = month if valid_month(month) else current_month()
    if request.method == "POST":
        category = request.form.get("category")
        amount = parse_amount(request.form.get("amount"))
        scope = request.form.get("scope", "recurring")
        b_month = None if scope == "recurring" else request.form.get("month")
        if category not in CATEGORIES["expense"] or amount is None or \
                (b_month is not None and not valid_month(b_month)):
            flash("Choose an expense category, a positive amount and a valid month.", "danger")
        else:
            existing = Budget.query.filter_by(user_id=current_user.id, category=category,
                                              month=b_month).first()
            if existing:
                existing.amount = amount
                flash(f"{category} budget updated to {pkr(amount)}.", "success")
            else:
                db.session.add(Budget(user_id=current_user.id, category=category,
                                      amount=amount, month=b_month))
                flash(f"{category} budget of {pkr(amount)} created.", "success")
            db.session.commit()
        return redirect(url_for("budgets", month=month))

    txs = user_tx().all()
    rows = budget_status(txs, current_user.budgets, month)
    spent_total = sum(t.amount for t in txs if t.type == "expense" and mkey(t.date) == month)
    budget_total = sum(r["budget"] for r in rows)
    unbudgeted = {c: v for c, v in category_totals(txs, month).items()
                  if c not in effective_budgets(current_user.budgets, month)}
    all_budgets = sorted(current_user.budgets, key=lambda b: (b.month or "", b.category))
    return render_template("budgets.html", month=month, rows=rows, spent_total=spent_total,
                           budget_total=budget_total, unbudgeted=unbudgeted, all_budgets=all_budgets,
                           alerts=[a for a in build_alerts(txs, current_user.budgets,
                                                           current_user.monthly_budget, month)
                                   if "budget" in a["title"].lower()])


def own_budget(budget_id):
    return Budget.query.filter_by(id=budget_id, user_id=current_user.id).first() or abort(404)


@app.route("/budgets/<int:budget_id>/edit", methods=["GET", "POST"])
@login_required
def edit_budget(budget_id):
    b = own_budget(budget_id)
    if request.method == "POST":
        amount = parse_amount(request.form.get("amount"))
        category = request.form.get("category")
        b_month = None if request.form.get("scope") == "recurring" else request.form.get("month")
        clash = Budget.query.filter(Budget.user_id == current_user.id, Budget.id != b.id,
                                    Budget.category == category, Budget.month.is_(b_month)
                                    if b_month is None else Budget.month == b_month).first()
        if category not in CATEGORIES["expense"] or amount is None or \
                (b_month is not None and not valid_month(b_month)):
            flash("Choose an expense category, a positive amount and a valid month.", "danger")
        elif clash:
            flash("A budget for that category and period already exists.", "danger")
        else:
            b.category, b.amount, b.month = category, amount, b_month
            db.session.commit()
            flash("Budget updated.", "success")
            return redirect(url_for("budgets"))
    return render_template("budget_form.html", b=b)


@app.route("/budgets/<int:budget_id>/delete", methods=["POST"])
@login_required
def delete_budget(budget_id):
    db.session.delete(own_budget(budget_id))
    db.session.commit()
    flash("Budget deleted.", "info")
    return redirect(url_for("budgets"))


@app.route("/budgets/overall", methods=["POST"])
@login_required
def overall_budget():
    raw = request.form.get("budget", "").strip()
    amount = 0.0 if raw in ("", "0") else parse_amount(raw)
    if amount is None:
        flash("Overall budget must be a positive number (or 0 to turn it off).", "danger")
    else:
        current_user.monthly_budget = amount
        db.session.commit()
        flash("Overall monthly budget saved." if amount else "Overall monthly budget turned off.", "success")
    return redirect(url_for("budgets"))


# ================================================================== reports
@app.route("/reports")
@login_required
def reports():
    txs = user_tx().all()
    mode = request.args.get("mode", "year")
    today = date.today()
    if mode == "month":
        m = request.args.get("month", "")
        m = m if valid_month(m) else current_month()
        start = date(int(m[:4]), int(m[5:]), 1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        title = month_label(m, "%B %Y")
    elif mode == "range":
        start = parse_date(request.args.get("start"), today.replace(day=1) - timedelta(days=180))
        end = parse_date(request.args.get("end"), today)
        if start > end:
            start, end = end, start
        title = f"{fdate(start)} – {fdate(end)}"
    else:
        mode = "year"
        year = request.args.get("year", type=int) or today.year
        if not 1901 <= year <= 2999:   # out-of-range years would crash date()
            year = today.year
        start, end = date(year, 1, 1), date(year, 12, 31)
        if year == today.year:  # don't show empty future months, unless future-dated entries exist
            last_tx = max((t.date for t in txs if t.date.year == year), default=today)
            end = max(today, last_tx)
            end = (end.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        title = str(year)

    sel = [t for t in txs if start <= t.date <= end]
    income = sum(t.amount for t in sel if t.type == "income")
    expense = sum(t.amount for t in sel if t.type == "expense")
    by_cat = category_totals(sel)
    inc_cat = category_totals(sel, tx_type="income")

    if mode == "month":  # daily breakdown
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        labels = [d.strftime("%d %b") for d in days]
        inc_s = [sum(t.amount for t in sel if t.type == "income" and t.date == d) for d in days]
        exp_s = [sum(t.amount for t in sel if t.type == "expense" and t.date == d) for d in days]
        rows = []
    else:
        keys = month_range(mkey(start), mkey(end))
        inc_m, exp_m = totals_by_month(sel, "income"), totals_by_month(sel, "expense")
        labels = [month_label(k) for k in keys]
        inc_s = [inc_m.get(k, 0) for k in keys]
        exp_s = [exp_m.get(k, 0) for k in keys]
        nonzero = [v for v in exp_s if v > 0]
        avg = sum(nonzero) / len(nonzero) if nonzero else 0
        rows = [{"key": k, "label": month_label(k, "%B %Y"), "income": i, "expense": e, "net": i - e,
                 "rate": (i - e) / i * 100 if i else None, "over": avg > 0 and e > avg * 1.2}
                for k, i, e in zip(keys, inc_s, exp_s)]

    overruns = []
    for k in month_range(mkey(start), mkey(end)):
        for r in budget_status(txs, current_user.budgets, k):
            if r["over"]:
                overruns.append({"month": k, **r})
    years = sorted({t.date.year for t in txs} | {today.year}, reverse=True)
    top = sorted((t for t in sel if t.type == "expense"), key=lambda t: -t.amount)[:5]
    return render_template(
        "reports.html", mode=mode, title=title, start=start, end=end, years=years,
        sel_year=start.year, sel_month=mkey(start), income=income, expense=expense,
        by_cat=by_cat, inc_cat=inc_cat, rows=rows, overruns=overruns, top=top, count=len(sel),
        chart={"labels": labels, "income": [round(v, 2) for v in inc_s],
               "expense": [round(v, 2) for v in exp_s],
               "net": [round(i - e, 2) for i, e in zip(inc_s, exp_s)],
               "over": [r["over"] for r in rows],
               "cat_labels": list(by_cat), "cat_values": [round(v, 2) for v in by_cat.values()]})


# ================================================================== AI prediction
@app.route("/prediction")
@login_required
def prediction():
    txs = user_tx().all()
    pred = prediction_for(txs)
    exp_m = totals_by_month(txs, "expense")
    logs = []
    for log in PredictionLog.query.filter_by(user_id=current_user.id) \
            .order_by(PredictionLog.target_month.desc()).limit(12):
        complete = log.target_month < current_month()
        actual = exp_m.get(log.target_month, 0.0) if complete else None
        logs.append({"log": log, "actual": actual,
                     "error": (log.predicted - actual) if actual is not None else None,
                     "error_pct": abs(log.predicted - actual) / actual * 100 if actual else None})
    return render_template("prediction.html", pred=pred, logs=logs, sample=has_sample_data())


# ================================================================== export / backup
@app.route("/data")
@login_required
def data():
    return render_template("data.html", count=user_tx().count(), budget_count=len(current_user.budgets))


@app.route("/export/csv")
@login_required
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Type", "Category", "Amount (PKR)", "Description"])
    for t in filtered_query(request.args).all():
        w.writerow([t.date.isoformat(), t.type, t.category, f"{t.amount:.2f}", safe_cell(t.description)])
    name = f"fintrack-transactions-{date.today().isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@app.route("/export/excel")
@login_required
def export_excel():
    items = filtered_query(request.args).all()
    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F5C3F")

    def header(ws, cols, widths):
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = head, fill
        for i, wdt in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = wdt
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Transactions"
    header(ws, ["Date", "Type", "Category", "Amount (PKR)", "Description"], (13, 10, 15, 15, 45))
    for t in items:
        ws.append([t.date, t.type.title(), t.category, t.amount, safe_cell(t.description)])
        ws.cell(ws.max_row, 5).data_type = "s"
        ws.cell(ws.max_row, 1).number_format = "DD MMM YYYY"
        ws.cell(ws.max_row, 4).number_format = "#,##0.00"

    ws2 = wb.create_sheet("Monthly Summary")
    header(ws2, ["Month", "Income", "Expenses", "Net Savings"], (14, 15, 15, 15))
    inc_m, exp_m = totals_by_month(items, "income"), totals_by_month(items, "expense")
    for k in sorted(set(inc_m) | set(exp_m)):
        ws2.append([month_label(k), inc_m.get(k, 0), exp_m.get(k, 0), inc_m.get(k, 0) - exp_m.get(k, 0)])
        for col in (2, 3, 4):
            ws2.cell(ws2.max_row, col).number_format = "#,##0"

    ws3 = wb.create_sheet("Budgets")
    header(ws3, ["Category", "Amount (PKR)", "Applies to"], (15, 15, 15))
    for b in current_user.budgets:
        ws3.append([b.category, b.amount, month_label(b.month) if b.month else "Every month"])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=f"fintrack-transactions-{date.today().isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def safe_cell(text):
    """Stop spreadsheet formula injection: text starting with = + - @ is stored as plain text."""
    text = text or ""
    return "'" + text if text[:1] in ("=", "+", "-", "@", chr(9), chr(13)) else text


def backup_payload(user):
    return {"app": "finance-tracker", "version": 2, "exported_at": utcnow().isoformat(),
            "username": user.username, "monthly_budget": user.monthly_budget,
            "budgets": [b.to_dict() for b in user.budgets],
            "transactions": [t.to_dict() for t in
                             Transaction.query.filter_by(user_id=user.id).order_by(Transaction.date)]}


@app.route("/backup")
@login_required
def backup():
    name = f"fintrack-backup-{date.today().isoformat()}.json"
    return Response(json.dumps(backup_payload(current_user), indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


class BackupError(ValueError):
    pass


def parse_backup(raw):
    """Validate a backup file completely before anything is changed."""
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackupError("The file is not valid JSON.")
    if not isinstance(data, dict) or data.get("app") != "finance-tracker" \
            or not isinstance(data.get("transactions"), list):
        raise BackupError("This is not a FinTrack backup file.")
    if len(data["transactions"]) > 50000:
        raise BackupError("Backup is too large (max 50,000 transactions).")
    txs, budgets = [], []
    for i, r in enumerate(data["transactions"], 1):
        if not isinstance(r, dict):
            raise BackupError(f"Transaction #{i} is invalid.")
        t_type, cat = r.get("type"), r.get("category")
        amount, d = parse_amount(r.get("amount")), parse_date(r.get("date"))
        if t_type not in CATEGORIES or cat not in CATEGORIES[t_type] or amount is None or d is None:
            raise BackupError(f"Transaction #{i} has an invalid type, category, amount or date.")
        txs.append({"type": t_type, "category": cat, "amount": amount, "date": d,
                    "description": str(r.get("description") or "")[:200]})
    for i, b in enumerate(data.get("budgets") or [], 1):
        amount = parse_amount(b.get("amount")) if isinstance(b, dict) else None
        if amount is None or b.get("category") not in CATEGORIES["expense"] or \
                (b.get("month") is not None and not valid_month(b.get("month"))):
            raise BackupError(f"Budget #{i} is invalid.")
        budgets.append({"category": b["category"], "amount": amount, "month": b.get("month")})
    overall = data.get("monthly_budget")
    overall = parse_amount(overall) if overall not in (None, 0, 0.0) else 0.0
    return txs, budgets, overall


@app.route("/restore", methods=["POST"])
@login_required
def restore():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a backup file to restore.", "danger")
        return redirect(url_for("data"))
    try:
        txs, budgets, overall = parse_backup(f.read())
    except BackupError as e:
        flash(f"Restore cancelled: {e} Your data was not changed.", "danger")
        return redirect(url_for("data"))

    replace = request.form.get("mode") == "replace"
    try:
        if replace:
            # keep a server-side safety copy of the current data first
            safety_dir = os.path.join(os.path.dirname(app.config["SQLALCHEMY_DATABASE_URI"][10:]), "backups")
            os.makedirs(safety_dir, exist_ok=True)
            with open(os.path.join(safety_dir, f"user{current_user.id}-pre-restore-"
                                               f"{datetime.now():%Y%m%d-%H%M%S}.json"), "w") as fh:
                json.dump(backup_payload(current_user), fh)
            user_tx().delete()
            Budget.query.filter_by(user_id=current_user.id).delete()
        db.session.add_all(Transaction(user_id=current_user.id, **t) for t in txs)
        for b in budgets:
            existing = Budget.query.filter_by(user_id=current_user.id, category=b["category"],
                                              month=b["month"]).first()
            if existing:
                existing.amount = b["amount"]
            else:
                db.session.add(Budget(user_id=current_user.id, **b))
        if overall is not None and (replace or not current_user.monthly_budget):
            current_user.monthly_budget = overall
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("restore failed")
        flash("Restore failed and was rolled back. Your data was not changed.", "danger")
        return redirect(url_for("data"))
    flash(f"Restored {len(txs)} transactions and {len(budgets)} budgets "
          f"({'replaced existing data' if replace else 'added to existing data'}).", "success")
    return redirect(url_for("dashboard"))


# ================================================================== settings
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            email = request.form.get("email", "").strip().lower()
            if "@" not in email or "." not in email.split("@")[-1] or len(email) > 120:
                flash("Please enter a valid email address.", "danger")
            elif User.query.filter(User.email == email, User.id != current_user.id).first():
                flash("That email is already used by another account.", "danger")
            else:
                current_user.email = email
                db.session.commit()
                flash("Profile updated.", "success")
        elif action == "password":
            if not current_user.check_password(request.form.get("current", "")):
                flash("Current password is incorrect.", "danger")
            elif len(request.form.get("new", "")) < 6:
                flash("New password must be at least 6 characters.", "danger")
            elif request.form.get("new") != request.form.get("confirm"):
                flash("New passwords do not match.", "danger")
            else:
                current_user.set_password(request.form["new"])
                db.session.commit()
                flash("Password changed.", "success")
        return redirect(url_for("settings"))
    sample_count = user_tx().filter(Transaction.description.like(SAMPLE_PREFIX + "%")).count()
    return render_template("settings.html", count=user_tx().count(), sample_count=sample_count)


SAMPLE_ITEMS = {
    "Food": ["Grocery shopping", "Restaurant dinner", "Bakery", "Fruit & vegetables"],
    "Transport": ["Fuel", "Ride hailing", "Metro card top-up"],
    "Bills": ["Electricity bill", "Gas bill", "Internet bill"],
    "Shopping": ["Clothing", "Household items", "Online order"],
    "Rent": ["House rent"],
    "Health": ["Pharmacy", "Doctor visit"],
    "Entertainment": ["Cinema", "Streaming subscription", "Weekend outing"],
}


@app.route("/sample-data", methods=["POST"])
@login_required
def sample_data():
    """Add 12 months of clearly-labelled demonstration data."""
    if has_sample_data():
        flash("Sample data is already loaded.", "info")
        return redirect(url_for("dashboard"))
    rnd = random.Random(current_user.id * 7919)
    base = {"Food": 22000, "Transport": 8000, "Bills": 12000, "Shopping": 7000,
            "Rent": 35000, "Health": 2500, "Entertainment": 3500}
    today = date.today()
    rows = []
    for i in range(12, -1, -1):
        first = datetime.strptime(add_months(current_month(), -i) + "-01", "%Y-%m-%d").date()
        last_day = today.day if i == 0 else 28
        growth = 1 + (12 - i) * 0.012
        seasonal = 1.15 if first.month in (6, 7, 12) else 1.0   # summer bills, December spending

        def add(t_type, cat, amount, day, desc):
            rows.append(Transaction(user_id=current_user.id, type=t_type, category=cat,
                                    amount=round(amount, -1), description=SAMPLE_PREFIX + desc,
                                    date=first + timedelta(days=min(day, last_day - 1))))
        add("income", "Salary", 150000, 0, "Monthly salary")
        if rnd.random() < 0.45 and last_day > 15:
            add("income", "Freelance", rnd.uniform(15000, 40000), 15, "Freelance project")
        for cat, amt in base.items():
            n = 1 if cat == "Rent" else (2 if cat in ("Bills", "Health") else 4)
            for _ in range(n):
                factor = seasonal if cat in ("Bills", "Shopping") else 1.0
                add("expense", cat, amt / n * growth * factor * rnd.uniform(0.75, 1.25),
                    rnd.randint(0, 27), rnd.choice(SAMPLE_ITEMS[cat]))
    db.session.add_all(rows)
    if not current_user.budgets:
        for cat, amt in (("Food", 25000), ("Transport", 9000), ("Bills", 13000),
                         ("Shopping", 8000), ("Entertainment", 4000)):
            db.session.add(Budget(user_id=current_user.id, category=cat, amount=amt))
    if not current_user.monthly_budget:
        current_user.monthly_budget = 95000
    db.session.commit()
    flash(f"Loaded {len(rows)} sample transactions (marked “Sample”) and example budgets.", "success")
    return redirect(url_for("dashboard"))


@app.route("/sample-data/remove", methods=["POST"])
@login_required
def remove_sample_data():
    n = user_tx().filter(Transaction.description.like(SAMPLE_PREFIX + "%")) \
        .delete(synchronize_session=False)
    db.session.commit()
    flash(f"Removed {n} sample transactions.", "info")
    return redirect(request.referrer and safe_next(urlparse(request.referrer).path) or url_for("dashboard"))


# ================================================================== misc
@app.route("/health")
def health():
    return {"status": "ok"}


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(500)
def handle_error(e):
    code = getattr(e, "code", 500) or 500
    messages = {400: "Bad request", 403: "Access denied", 404: "Page not found",
                413: "File too large (maximum 5 MB)", 500: "Something went wrong"}
    detail = e.description if code == 400 and getattr(e, "description", None) else None
    if code == 500:
        db.session.rollback()
    return render_template("error.html", code=code, title=messages.get(code, "Error"),
                           detail=detail), code


if __name__ == "__main__":
    # Local development server. Listens on this computer only unless HOST is set (Docker uses Gunicorn instead).
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", 5000))
    print(f"\n  FinTrack is running:  http://localhost:{port}\n  Press CTRL+C to stop.\n")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
