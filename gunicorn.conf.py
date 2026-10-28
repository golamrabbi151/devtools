"""Gunicorn settings (production). Everything is overridable with environment variables.

Why these values (measured, see README "Capacity"):
- gthread workers: a few stalled/slow clients freeze plain "sync" workers (3 sockets = whole site down).
- workers ~= CPU cores (the work is CPU-bound Python); threads give I/O concurrency inside each worker.
- max_requests recycles workers regularly so any slow memory growth never accumulates.
"""
import multiprocessing
import os

cores = multiprocessing.cpu_count()
bind = os.environ.get("BIND", "0.0.0.0:8000")
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", max(2, min(cores, 8))))
threads = int(os.environ.get("WEB_THREADS", 4))
timeout = int(os.environ.get("WEB_TIMEOUT", 30))            # a stuck request is killed after this
graceful_timeout = 20
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
worker_tmp_dir = "/dev/shm"                                  # heartbeat files in RAM (avoids slow-disk false timeouts)
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 8190
accesslog = "-"
access_log_format = "%(h)s %(m)s %(U)s %(s)s %(b)s %(L)s"    # no query strings / headers in logs
