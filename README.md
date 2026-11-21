# FinTrack: Personal Finance Tracker with Expense Prediction

A Python web application for recording income and expenses, analysing spending with interactive charts, and **predicting next month's expenses with machine learning**. It's packaged as a Docker image and deployed on Render.

> Project: *Personal Finance Tracker with Expense Prediction*. Domain: Machine Learning / Web.

**Live demo:** `https://<your-app>.onrender.com` (replace after deploying)

---

## Features

| Module | What it does |
|---|---|
| **Authentication & Dashboard** | Secure registration and login (hashed passwords). The dashboard shows balance, total income and expenses, this month's spending, charts, recent entries and the AI prediction. |
| **Income & Expense Management** | Add, edit, view and delete entries. Categories include Food, Transport, Bills, Shopping, Rent, Salary and Freelance. Filter by type, category, date range, amount range or description. |
| **Visualization & Reports** | Monthly and yearly reports with interactive Plotly charts (income vs. expense, net savings, spending by category). Months more than 20% above average are highlighted. Overspending alerts flag categories well above their usual level and a broken monthly budget. |
| **Expense Prediction (ML)** | scikit-learn **Linear Regression** and **Random Forest** models are trained on the user's monthly expense history to predict next month's total. Shows the trend (rising or falling) and warns if the prediction exceeds the budget. |
| **Export & Backup** | Export (filtered) data to **CSV** or **Excel (.xlsx)**. Full **JSON backup**, with restore in merge or replace mode. |
| **Demo data** | One click loads 12 months of realistic sample data, so the charts and prediction work right away. |

## Tech stack

- **Frontend:** HTML, CSS, Bootstrap 5, JavaScript, Plotly.js
- **Backend:** Python 3.12, Flask, Flask-Login, Flask-SQLAlchemy
- **Database:** SQLite
- **Machine learning:** scikit-learn (LinearRegression, RandomForestRegressor), NumPy
- **Export:** csv, openpyxl
- **Deployment:** Docker, docker-compose, Gunicorn, Render

## How the expense prediction works

1. All of the user's expenses are grouped into **monthly totals**. Months with no spending count as 0.
2. The current month is left out of training because it isn't finished yet.
3. **Linear Regression** fits a straight line through the monthly totals (x = month number, y = total spent) and extends it to next month. This captures the overall trend.
4. **Random Forest** (used once there are at least 5 months of data) learns from the previous 2 months' totals to predict the next one. This captures recent patterns.
5. The final prediction is the **average of both models**. It appears on the dashboard together with each model's value, the trend direction and the monthly average.

At least 2 past months of expenses are needed before a prediction can be made.

## Project structure

```
finance-tracker/
├── app.py               # Flask routes: auth, CRUD, filters, reports, export, backup
├── models.py            # Database models (User, Transaction) and categories
├── ml.py                # Expense prediction module (scikit-learn)
├── templates/           # Jinja2 HTML pages
├── static/style.css     # Custom styling
├── tests/test_app.py    # Automated tests
├── requirements.txt
├── Dockerfile           # Production Docker image (Gunicorn)
├── docker-compose.yml   # One-command local run with a persistent volume
└── render.yaml          # Render deployment blueprint
```

## Run locally (without Docker)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000, register an account, and click **Load sample data**.

Local demo account (only in the local `instance/finance.db`): username `demo`, password `demo123`.

Run the tests:
```bash
python -m unittest discover tests -v
```

## Run with Docker

```bash
docker compose up --build -d
```
Open http://localhost:5000. Data is stored in the `finance-data` volume, so it survives restarts.

Or use plain Docker commands:
```bash
docker build -t finance-tracker .
docker run -d -p 5000:5000 --name finance-tracker finance-tracker
```

Stop it with `docker compose down`.

## Deploy on Render (free)

1. Push this folder to a GitHub repository.
2. Sign in to https://render.com with GitHub.
3. Click **New → Blueprint**, select the repository, and Render reads `render.yaml`.
   (Or click **New → Web Service**, choose the repo and pick **Docker** as the runtime.)
4. Click **Deploy**. Render builds the Docker image and gives you a public URL.

> **Note:** On Render's free plan the filesystem resets whenever the service restarts or redeploys, so SQLite data is temporary. Use **Settings → Download backup** to keep your data, or attach a paid persistent disk mounted at `/app/data`. The free service also sleeps after 15 minutes without traffic, so the first request afterwards takes about 30–50 seconds.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | dev value | Flask session signing key (set a random value in production) |
| `DATABASE_PATH` | `instance/finance.db` | SQLite file location |
| `PORT` | `5000` | Port Gunicorn listens on (set automatically by Render) |
