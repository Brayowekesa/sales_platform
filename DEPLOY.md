# Putting the shop online

The full guide is **[docs/OPERATIONS.md](docs/OPERATIONS.md)**: settings, first deployment with
Docker, managed platforms (Render, Railway, Fly.io), updating, backups and restore, moving from
SQLite to PostgreSQL, monitoring, the security checklist and troubleshooting.

The short version, on an Ubuntu server with Docker, from `/opt/shop`:

```bash
cp .env.example .env && nano .env && chmod 600 .env   # DOMAIN, POSTGRES_PASSWORD, FLASK_SECRET_KEY, MAIL_*, MPESA_*
docker compose up -d --build
docker compose exec web flask --app app create-admin
```

Then set up nightly backups (`deploy/backup.sh`, which saves the database, `static/uploads` and
`private_uploads`). On managed platforms, both `static/uploads` **and** `private_uploads` need a
persistent disk. Details for every step are in [docs/OPERATIONS.md](docs/OPERATIONS.md).
