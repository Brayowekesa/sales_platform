#!/bin/sh
# Upgrade the database once, then start the web server.
set -e
flask --app app init-db
exec gunicorn -c docker/gunicorn.conf.py app:app
