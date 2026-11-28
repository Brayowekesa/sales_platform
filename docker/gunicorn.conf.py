"""Gunicorn settings for the Docker image (override with environment variables)."""
import multiprocessing
import os

bind = '0.0.0.0:8000'
workers = int(os.environ.get('WEB_WORKERS', min(2 * multiprocessing.cpu_count() + 1, 5)))
threads = int(os.environ.get('WEB_THREADS', 4))
worker_class = 'gthread'
timeout = 60            # M-Pesa requests can take a few seconds
graceful_timeout = 30
keepalive = 5
accesslog = '-'         # logs go to `docker compose logs web`
errorlog = '-'
forwarded_allow_ips = '*'   # only Caddy can reach this port inside the Docker network
