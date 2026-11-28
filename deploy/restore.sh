#!/bin/sh
# Restore a backup made by backup.sh:  sh deploy/restore.sh backups/db_2026-01-01_0215.dump [backups/uploads_2026-01-01_0215.tar.gz]
# WARNING: replaces the current database (and photos, if given).
set -eu
umask 077
cd "$(dirname "$0")/.."
DB_FILE=${1:?Usage: sh deploy/restore.sh backups/db_XXXX.dump [backups/uploads_XXXX.tar.gz]}
printf "This replaces the live database with %s. Type YES to continue: " "$DB_FILE"; read -r answer
[ "$answer" = "YES" ] || { echo "Cancelled."; exit 1; }
docker compose stop web
docker compose exec -T db pg_restore -U shop -d shop --clean --if-exists --no-owner < "$DB_FILE"
if [ "${2:-}" ]; then
  docker compose run --rm --no-deps -T --entrypoint sh web -c "rm -rf /app/static/uploads/* /app/private_uploads/* && tar xzf - -C /app" < "$2"
fi
docker compose start web
echo "Restored."
