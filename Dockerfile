FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Run as a normal user. /data is the persistent volume: the SQLite file (sessions, cases, runs, the daily Claude budget) lives there.
RUN useradd --system --uid 10001 --home-dir /app app && mkdir -p /data && chown app:app /data
USER app
ENV RUNS_DB=/data/runs.db PORT=8000
VOLUME /data
EXPOSE 8000
# Healthy means the app is configured (login, password and secret set) and the database answers. It never calls Claude.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz',timeout=4)" || exit 1
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
