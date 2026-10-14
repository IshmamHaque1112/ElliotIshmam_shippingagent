FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV RUNS_DB=/data/runs.db PORT=8000
VOLUME /data
EXPOSE 8000
# One worker on purpose: the Claude usage cap, the assessment cache and the simulation clock live in process memory.
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8000", "app:app"]
