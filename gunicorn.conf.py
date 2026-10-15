"""Production settings for the container. Everything can be overridden with environment variables.

Several workers are fine now: the daily Claude budget, sessions and each session's clock all live in the SQLite file
(WAL mode) on the persistent volume, not in process memory. The one exception is the shared clock used by scripts
that send a Basic header: it is per process, so use a signed-in session for anything that moves the simulated clock.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
# A Claude call can retry (ANTHROPIC_TIMEOUT_SECONDS x retries), so a request may legitimately run for minutes.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus'   # no query strings with secrets, no cookies, no headers
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
