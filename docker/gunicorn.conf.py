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
# Worker heartbeat files: /dev/shm is in memory and stays writable when the container's file system is read-only
worker_tmp_dir = '/dev/shm' if os.path.isdir('/dev/shm') else None
# Caddy already writes the access log (with tokens and query strings removed - see deploy/Caddyfile).
# Gunicorn's own access log would record full URLs (password-reset and rider links), so it is off
# unless WEB_ACCESS_LOG=1.
accesslog = '-' if os.environ.get('WEB_ACCESS_LOG') == '1' else None
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(L)s'   # path only, no query string
errorlog = '-'          # errors go to `docker compose logs web`
forwarded_allow_ips = '*'   # only Caddy can reach this port inside the Docker network
