#!/bin/sh
# Back up the database and uploaded photos into ./backups (keeps 14 days).
# Run from the project folder, e.g. every night with cron:
#   15 2 * * * cd /opt/sales_platform && sh deploy/backup.sh >> backups/backup.log 2>&1
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H%M)
docker compose exec -T db pg_dump -U shop -d shop --format=custom > "backups/db_$STAMP.dump"
docker compose exec -T web tar czf - -C /app static/uploads private_uploads > "backups/uploads_$STAMP.tar.gz"
find backups -name 'db_*.dump' -mtime +14 -delete
find backups -name 'uploads_*.tar.gz' -mtime +14 -delete
echo "$(date): backup $STAMP done ($(du -sh backups | cut -f1) in backups/)"
# Copy ./backups somewhere off this server too (another server, cloud storage) - see DEPLOY.md.
