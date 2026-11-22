# ---- Personal Finance Tracker with Expense Prediction ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    DATABASE_PATH=/app/data/finance.db

WORKDIR /app

# Install dependencies first so Docker can cache this layer
# wheels/ is optional: pre-downloaded packages for offline or slow-network builds.
# If it is empty, pip downloads from PyPI as usual (e.g. on Render).
COPY requirements.txt .
COPY wheels/ /wheels/
RUN (pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
     || pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt) \
    && rm -rf /wheels

# Copy only the application code (no database, secrets, tests or build files)
COPY app.py finance.py ml.py models.py migrate.py ./
COPY templates/ templates/
COPY public/ public/

# Run as a non-root user for security
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health')"

# Gunicorn production server; PORT is provided by Render (defaults to 5000)
# --preload loads the app (and creates DB tables) once before forking workers
CMD ["sh", "-c", "exec gunicorn --preload --bind 0.0.0.0:${PORT} --workers 2 --timeout 60 app:app"]
