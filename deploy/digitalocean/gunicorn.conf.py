import multiprocessing
import os


bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8000")
workers = int(
    os.getenv("GUNICORN_WORKERS", max(2, multiprocessing.cpu_count() * 2 + 1))
)
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
accesslog = "-"
errorlog = "-"
capture_output = True
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
