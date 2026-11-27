# Operations guide

For whoever runs the server. It covers settings, the first deployment with Docker, updates,
backups, moving from SQLite to PostgreSQL, monitoring, security and troubleshooting.

For day-to-day shop work (orders, products, riders) see the [admin guide](ADMIN_GUIDE.md).

Contents:

1. [Settings (environment variables)](#1-settings-environment-variables)
2. [First deployment with Docker](#2-first-deployment-with-docker)
3. [Updating](#3-updating)
4. [Backups and restore](#4-backups-and-restore)
5. [Moving from SQLite to PostgreSQL](#5-moving-from-sqlite-to-postgresql)
6. [Monitoring and health check](#6-monitoring-and-health-check)
7. [Security checklist](#7-security-checklist)
8. [Troubleshooting](#8-troubleshooting)
9. [Useful commands](#9-useful-commands)

---

## 1. Settings (environment variables)

Settings are read from environment variables. The shop also reads a `.env` file in the project
folder (copy `.env.example` to `.env`). With Docker, `docker-compose.yml` passes `.env` to the shop.
An empty value counts as "not set", so the default is used.

Shop contact details (phone, WhatsApp, address, opening hours, social links) are **not** here:
set them in **Admin → Shop settings**.

### Server (Docker only)

| Name | Required? | What it does | Example |
|---|---|---|---|
| `DOMAIN` | Yes (Docker) | Your domain. Caddy gets the HTTPS certificate for it, and `www.DOMAIN` redirects to it. | `shop.example.co.ke` |
| `POSTGRES_PASSWORD` | Yes (Docker) | Password of the PostgreSQL database. Letters and numbers only (it goes into a URL). | output of `python3 -c "import secrets; print(secrets.token_hex(24))"` |
| `WEB_WORKERS` | No | Number of Gunicorn worker processes. Default: 2 × CPUs + 1, at most 5. | `3` |
| `WEB_THREADS` | No | Threads per worker. Default 4. Keep it at most about half of `DB_POOL_SIZE` (see below). | `4` |

**Threads and database connections.** Each Gunicorn worker has its own pool of at most
`DB_POOL_SIZE` PostgreSQL connections, shared by its `WEB_THREADS` threads. One request can hold
**two** connections at once (e.g. while it writes the activity log), so keep `WEB_THREADS` at most
about half of `DB_POOL_SIZE` (the defaults, 4 threads and a pool of 10, are fine); otherwise busy
threads wait for a free connection. In total the shop may open `WEB_WORKERS × DB_POOL_SIZE`
connections: keep that below PostgreSQL's `max_connections` (100 by default, often less on managed
databases).

### Basics

| Name | Required? | What it does | Example |
|---|---|---|---|
| `FLASK_SECRET_KEY` | **Yes** on the live site | Signs logins, password-reset links, rider and gift links and the M-Pesa callback. Without it a random key is used on every start: everyone is logged out on restart, and Gunicorn workers will not share logins. | output of `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_DEBUG` | No | `1` only on your own computer. Never on the live site. | `0` |
| `FLASK_HOST` | No | Address used by `python app.py` only. Default `127.0.0.1`. | `127.0.0.1` |
| `PORT` | No | Port used by `python app.py` only. Default 5000. | `5000` |
| `SESSION_COOKIE_SECURE` | Live: `1` | `1` = cookies only over HTTPS, and the HSTS header is sent. Docker sets `1`. | `1` |
| `TRUSTED_PROXY_COUNT` | Live: `1` | How many proxies sit in front of the shop (Caddy/nginx = 1). Needed for real visitor IPs (login limits, M-Pesa IP check) and correct `https://` links. Docker sets `1`. Leave `0` with no proxy. | `1` |
| `SITE_NAME` | No | Shop name in pages, emails, SMS and the app name. Default `ESA E-Shop`. | `ESA E-Shop` |
| `CURRENCY_SYMBOL` | No | Default `KSh`. | `KSh` |
| `SHOP_TIMEZONE` | No | Times are stored in UTC and shown in this zone. Default `Africa/Nairobi`. | `Africa/Nairobi` |
| `REQUIRE_ADMIN_2FA` | Live: `1` | `1` = admins must turn on two-step login before the admin panel opens. Docker sets `1`. | `1` |
| `ALLOWED_REGISTER_EMAILS` | No | Comma-separated list. If set, only these emails can register. Empty = anyone can register. | `you@example.com` |
| `RIDER_LINK_DAYS` | No | How many days a rider link works. Default 7. | `7` |
| `LOW_STOCK_THRESHOLD` | No | Products with this many or fewer left count as "low stock" on the admin dashboard and Low Stock Report. Default 5. | `5` |
| `SENTRY_DSN` | No | Send crash reports to [sentry.io](https://sentry.io). Customer data is not sent. | `https://...@...ingest.sentry.io/...` |

### Database

| Name | Required? | What it does | Example |
|---|---|---|---|
| `DATABASE_URL` | Live: yes (Docker sets it) | PostgreSQL address. Empty = use the SQLite file. **Leave empty in `.env` with Docker** - compose sets it. | `postgresql://shop:PASSWORD@db:5432/shop` |
| `DATABASE_PATH` | No | SQLite file, used when `DATABASE_URL` is empty. Default `sales.db` in the project folder. | `/srv/shop/sales.db` |
| `DB_POOL_SIZE` | No | Most PostgreSQL connections per worker. Default 10. At least about 2 × `WEB_THREADS`. | `10` |
| `AUTO_MIGRATE` | No | `1` (default) = upgrade the database every time the app starts. Docker sets `0` because `docker/entrypoint.sh` runs `init-db` once before Gunicorn starts. | `0` |
| `PROOF_FOLDER` | No | Where packing-proof photos/videos are kept (private, not served as public files). Default `private_uploads/proofs`. | `/app/private_uploads/proofs` |

### Email

Used for verification codes, password resets, order updates and wishlist alerts. Any SMTP provider works.

| Name | Required? | What it does | Example |
|---|---|---|---|
| `MAIL_SERVER` | Yes for email | SMTP server. Default `smtp.gmail.com`. | `smtp.gmail.com`, `smtp-relay.brevo.com`, `smtp.zoho.com` |
| `MAIL_PORT` | No | Default 587. | `587` |
| `MAIL_USE_TLS` | No | `1` (default) for port 587. | `1` |
| `MAIL_USE_SSL` | No | `1` for port 465 (then set `MAIL_USE_TLS=0`). Default `0`. | `0` |
| `MAIL_USERNAME` | Yes for email | SMTP login. | `orders@example.com` |
| `MAIL_APP_PASSWORD` | Yes for email | SMTP password. For Gmail, an **app password** (needs 2-step verification on the Google account), not your normal password. | (secret) |
| `MAIL_DEFAULT_SENDER` | No | "From" address. Default: `MAIL_USERNAME`. | `ESA E-Shop <orders@example.com>` |

Order and alert emails are only sent when `MAIL_USERNAME`, `MAIL_APP_PASSWORD` and a sender are all set.

### SMS (Africa's Talking, optional)

SMS is **on** when both `AT_USERNAME` and `AT_API_KEY` are set, and off otherwise. Customers then get
order updates by SMS, gift receivers get their texts, and wishlist alerts go by SMS too.

| Name | Required? | What it does | Example |
|---|---|---|---|
| `AT_USERNAME` | For SMS | Your Africa's Talking app username. `sandbox` uses their test system (`api.sandbox.africastalking.com`); messages then only appear in the sandbox simulator, not on real phones. | `sandbox` |
| `AT_API_KEY` | For SMS | API key from the Africa's Talking dashboard (sandbox and live have different keys). | (secret) |
| `AT_SENDER_ID` | No | An approved sender name. Empty = Africa's Talking's default sender. | `ESASHOP` |

Phone numbers like `0712 345 678` are changed to `+254712345678` before sending.

### M-Pesa (Daraja)

| Name | Required? | What it does | Example |
|---|---|---|---|
| `MPESA_CONSUMER_KEY` | For M-Pesa | From your Daraja app. | (secret) |
| `MPESA_CONSUMER_SECRET` | For M-Pesa | From your Daraja app. | (secret) |
| `MPESA_BUSINESS_SHORT_CODE` | For M-Pesa | Your paybill number. Default `174379` (Safaricom's test paybill). | `174379` |
| `MPESA_PASSKEY` | For M-Pesa | The Lipa na M-Pesa Online passkey. | (secret) |
| `MPESA_CALLBACK_URL` | For M-Pesa | Public HTTPS address Safaricom calls with the result. The shop adds `?order_id=...&sig=...` itself. | `https://shop.example.co.ke/mpesa_callback` |
| `MPESA_CALLBACK_ALLOWED_IPS` | No | Comma-separated IPs or ranges. If set, callbacks from any other IP are refused (403). Use the list in Safaricom's Daraja documentation. | `203.0.113.10,203.0.113.0/24` (placeholder - use Safaricom's list) |

**Important - sandbox only for now.** The code in `shop/payments.py` always calls Safaricom's
**sandbox** addresses (`sandbox.safaricom.co.ke`) and sends `CustomerPayBillOnline`. Real
payments need a code change to use the live addresses (and `CustomerBuyGoodsOnline` for a till
number). Test with the sandbox credentials until that is done.

---

## 2. First deployment with Docker

You need:

- A Linux server (Ubuntu 22.04 or 24.04, 1-2 GB RAM) with SSH access as a sudo user.
- A domain name. In its DNS settings, add an **A record** for `@` (and `www`) pointing to the
  server's IP. DNS changes can take up to an hour.

### Steps

1. **Install Docker** (once), then log out and back in:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   ```

2. **Get the code:**

   ```bash
   sudo mkdir -p /opt/shop && sudo chown $USER /opt/shop
   git clone https://github.com/Brayowekesa/sales_platform.git /opt/shop
   cd /opt/shop
   ```

3. **Fill in the settings:**

   ```bash
   cp .env.example .env
   nano .env
   ```

   At least: `DOMAIN`, `POSTGRES_PASSWORD`, `FLASK_SECRET_KEY`, the `MAIL_*` settings and the
   `MPESA_*` settings (`MPESA_CALLBACK_URL=https://YOUR_DOMAIN/mpesa_callback`). Leave
   `DATABASE_URL` empty. Protect the file: `chmod 600 .env`.

4. **Open only the ports you need:**

   ```bash
   sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
   ```

5. **Start:**

   ```bash
   docker compose up -d --build
   docker compose ps            # db, web and caddy "Up"; web becomes "healthy"
   docker compose logs -f web   # Ctrl+C to stop watching
   ```

   On start the `web` container runs `flask --app app init-db` (creates the tables and the default
   delivery options), then starts Gunicorn.

6. **Open** `https://YOUR_DOMAIN`. The first visit can take a few seconds while Caddy gets the certificate.

7. **Create your admin account:**

   ```bash
   docker compose exec web flask --app app create-admin
   ```

8. **Log in and turn on two-step login.** On Docker it is required before the admin panel opens.
   Have an authenticator app ready on your phone (Google Authenticator, Microsoft Authenticator or
   Authy), and save the recovery codes it shows you.

9. **Fill in Admin → Shop settings and Admin → Delivery.**

10. **Set up nightly backups** ([section 4](#4-backups-and-restore)).

11. In the Daraja portal, make sure the callback URL is `https://YOUR_DOMAIN/mpesa_callback`.

### Managed platforms (Render, Railway, Fly.io)

The `Dockerfile` works there too. Create a PostgreSQL database and set `DATABASE_URL`; set the other
variables plus `TRUSTED_PROXY_COUNT=1`, `SESSION_COOKIE_SECURE=1`, `REQUIRE_ADMIN_2FA=1` and
`AUTO_MIGRATE=0` (the entrypoint still migrates on start). Attach persistent disks at
`/app/static/uploads` **and** `/app/private_uploads`, or photos and packing proofs are lost on every
deploy (`private_uploads` holds the packing-proof photos/videos, which are deliberately kept out of
`static/` so they are never public: it needs its own persistent disk just like the photos; if your
plan allows only one disk, use the Docker setup on a VPS instead). The platform
provides HTTPS, so Caddy is not needed. Backups: `deploy/backup.sh` is for the Docker setup; on a
managed platform use its database backups and copy both upload disks yourself.

---

## 3. Updating

```bash
cd /opt/shop
sh deploy/backup.sh          # always back up first
git pull
docker compose up -d --build
docker compose logs --tail 50 web
```

### Database changes (migrations)

Database changes are numbered migrations in `shop/db.py`. `flask --app app init-db` applies any that
have not run yet and prints the version, e.g. `Database ready (postgres, schema version 6).` It is
safe to run again.

- **Docker:** `docker/entrypoint.sh` runs `init-db` once before Gunicorn starts, and compose sets
  `AUTO_MIGRATE=0` so the workers do not each try again.
- **Without Docker:** with `AUTO_MIGRATE=1` (the default) the app migrates when it starts. Or run
  `flask --app app init-db` yourself after updating.
- On PostgreSQL a lock makes sure only one process migrates at a time.

Migrations only add tables and columns. Still, back up first: there is no "undo" for a migration
except restoring a backup.

---

## 4. Backups and restore

### What a backup contains

`sh deploy/backup.sh` (run from the project folder, with the containers running) writes two files to
`backups/`:

| File | Contents |
|---|---|
| `db_YYYY-MM-DD_HHMM.dump` | The whole PostgreSQL database (`pg_dump`, custom format): customers, orders, products, settings, logs. |
| `uploads_YYYY-MM-DD_HHMM.tar.gz` | `static/uploads` (product and promotion photos, plus resized copies) **and** `private_uploads` (packing-proof photos/videos). |

Backups older than 14 days are deleted. Backups contain customer data: keep them private.

### Nightly backups

Add to `crontab -e`:

```
15 2 * * * cd /opt/shop && sh deploy/backup.sh >> backups/backup.log 2>&1
```

**Keep a copy off the server.** If the server is lost, backups on it are lost too. For example,
copy `backups/` every night to cloud storage with [rclone](https://rclone.org).

### Restore

This **replaces** the live database (and the photos and proofs, if you give the second file). It asks
you to type `YES`:

```bash
sh deploy/restore.sh backups/db_2026-01-31_0215.dump backups/uploads_2026-01-31_0215.tar.gz
```

The script stops `web`, restores the database with `pg_restore --clean`, empties and restores
`static/uploads` and `private_uploads`, then starts `web` again. The `db` container must be running.

Test a restore on a spare server now and then, so you know the backups work.

### Without Docker (SQLite)

Stop the shop and copy `sales.db` (plus `sales.db-wal` and `sales.db-shm` if they exist),
`static/uploads/` and `private_uploads/`.

---

## 5. Moving from SQLite to PostgreSQL

`flask --app app copy-to-postgres SQLITE_FILE` copies an old SQLite shop into the PostgreSQL
database in `DATABASE_URL`.

- It first creates the latest tables in PostgreSQL. Columns that exist in both databases are copied;
  very old SQLite files (from before migrations) get their missing columns added first.
- It **refuses to run** if the PostgreSQL database already has users or orders.
- It copies users, products, price history, Pamoja deals, promotions, delivery options, settings,
  orders, order items, order history, packing-proof records, reviews, wishlists, carts, product
  photos and activity logs.
- Price history is copied, so verified "was" prices stay verified. Only products with no history at
  all (very old SQLite files) start their history on the day of the copy, and their "was" prices
  must pass the 14-day rule again.
- It copies database rows only. Copy the files too: `static/uploads` (photos) **and**
  `private_uploads` (the packing-proof files the `order_proofs` rows point to).

With Docker:

```bash
docker compose cp sales.db web:/tmp/sales.db
docker compose exec web flask --app app copy-to-postgres /tmp/sales.db
docker compose cp static/uploads/. web:/app/static/uploads/
docker compose cp private_uploads/. web:/app/private_uploads/
```

Then check a few products and orders in the admin panel.

---

## 6. Monitoring and health check

- **Health check:** `GET /healthz` returns `{"status": "ok"}` (HTTP 200) when the shop and its
  database respond, and `{"status": "error"}` (HTTP 503) when the database does not. Docker uses it
  every 30 seconds (`docker compose ps` shows "healthy"). Point a free uptime checker (UptimeRobot,
  Better Stack) at `https://YOUR_DOMAIN/healthz`.
- **Errors:** set `SENTRY_DSN` to get crash reports by email from [sentry.io](https://sentry.io).
  Otherwise check `docker compose logs web` regularly.
- **Logs:** Gunicorn writes requests and errors to `docker compose logs web`. Caddy logs requests too.
- **Activity Logs** in the admin panel record logins, order changes, M-Pesa results and rejected callbacks.
- **Code checks:** every push to GitHub runs the tests (SQLite and PostgreSQL), pyflakes, `pip-audit`
  and a Docker build (`.github/workflows/checks.yml`). The vulnerability scan also runs every Monday.
  If it fails, update the package it names in `requirements.txt`.

---

## 7. Security checklist

- [ ] `FLASK_SECRET_KEY` is a long random value, **fixed** (the same on every restart) and secret.
      Changing it logs everyone out, breaks open rider and gift links, and makes Safaricom's callbacks
      for payments in progress fail the signature check.
- [ ] `POSTGRES_PASSWORD` is long and random.
- [ ] `REQUIRE_ADMIN_2FA=1` (Docker sets it), and every admin has saved their recovery codes.
- [ ] `SESSION_COOKIE_SECURE=1` and `FLASK_DEBUG=0` on the live site.
- [ ] **Never commit** `.env`, database files (`sales.db`), `backups/`, `static/uploads/` or
      `private_uploads/`. They are in `.gitignore` - check `git status` before every commit.
- [ ] If the Gmail app password (or any key) was ever shared, pasted in a chat or committed,
      **revoke it and make a new one** (Google account → Security → App passwords), then update `.env`
      and run `docker compose up -d`. Do the same for M-Pesa and Africa's Talking keys.
- [ ] Only ports 22, 80 and 443 are open. SSH uses keys, not passwords.
- [ ] Automatic security updates: `sudo apt install unattended-upgrades`.
- [ ] Nightly backups run, and a copy goes off the server.
- [ ] Optional: `MPESA_CALLBACK_ALLOWED_IPS` set to Safaricom's published IPs.
- [ ] `SENTRY_DSN` set, or logs checked regularly.
- [ ] Give admin rights only to staff who need them; remove them when someone leaves.

---

## 8. Troubleshooting

### SMS are not sending

1. Both `AT_USERNAME` and `AT_API_KEY` must be set. On the admin order page the text under
   **Update Status** says "notified by email **and SMS**" only when SMS is on.
2. With `AT_USERNAME=sandbox`, messages only go to the Africa's Talking **simulator**, never to real
   phones. Use your live app username and live API key.
3. A live account needs airtime (credit) on Africa's Talking.
4. `AT_SENDER_ID` must be a sender name approved for your account; otherwise leave it empty.
5. Look for `SMS to ... failed` in `docker compose logs web`.
6. After changing `.env`, run `docker compose up -d` so the container gets the new values.

### M-Pesa: no prompt, or orders stuck in "Pending Payment"

- **"M-Pesa is not reachable right now"** at checkout: the shop could not get a token or start the
  payment. Check the consumer key/secret, short code and passkey, and look for
  `M-Pesa RequestException` in the logs. Remember the code uses the **sandbox** (see
  [M-Pesa settings](#m-pesa-daraja)).
- **Prompt arrives but the order stays "Pending Payment":** Safaricom could not reach the callback.
  - `MPESA_CALLBACK_URL` must be public HTTPS, e.g. `https://YOUR_DOMAIN/mpesa_callback`
    (not `localhost`). For testing on your own computer, use a tunnel.
  - Check Activity Logs or the web logs for **"M-Pesa callback rejected (bad signature)"** - usually
    `FLASK_SECRET_KEY` changed between the payment starting and the callback - or
    **"(IP not allowed)"** - `MPESA_CALLBACK_ALLOWED_IPS` is wrong, or `TRUSTED_PROXY_COUNT` is not
    `1` behind Caddy, so every callback looks like it comes from Caddy.
  - **"M-Pesa amount mismatch"**: the customer paid less than the total; the order is left as it is.
- Orders with no callback are never cancelled automatically. Staff should check and cancel them.

### Emails are not arriving

- Order emails need `MAIL_USERNAME`, `MAIL_APP_PASSWORD` and a sender. Check spelling: the
  password variable is `MAIL_APP_PASSWORD`.
- Gmail: use an **app password**. If you changed your Google password, the old app password stops
  working - make a new one.
- Port 587 needs `MAIL_USE_TLS=1`, port 465 needs `MAIL_USE_SSL=1` and `MAIL_USE_TLS=0`.
- Some VPS providers block outgoing SMTP ports. Ask them, or use a provider such as Brevo.
- Look for `Order email for #... failed` or `Failed to send verification email` in the logs, and in
  Activity Logs for verification and reset emails.
- Check the customer's spam folder. Using a sender on your own domain helps.
- On your own computer with `FLASK_DEBUG=1`, a failed verification email writes the code to the terminal.

### Uploads fail or are "too large"

The limits, from outside in:

| Where | Limit |
|---|---|
| Caddy (`deploy/Caddyfile`, `request_body max_size`) | 32 MB per request |
| The shop (`MAX_CONTENT_LENGTH` in `shop/config.py`) | 30 MB per request (all files together) |
| Packing-proof video | 25 MB each, 10 files per order |
| Product photos | 8 per product; PNG, JPG, GIF or WebP |

- A request bigger than 30 MB gets the page "That file is too big - the limit is 30 MB". Upload
  fewer files at a time, or shorter/lower-quality videos. (A request bigger than Caddy's limit gets
  Caddy's own plain error instead.)
- If you raise `MAX_CONTENT_LENGTH`, also raise `max_size` in the Caddyfile and restart Caddy
  (`docker compose restart caddy`), or Caddy refuses the upload first.
- "is not a PNG, JPG, GIF or WebP image": the file's content is checked, not just its name. HEIC photos
  from iPhones must be converted (or set the camera to "Most compatible").
- Photos missing after a deploy: `static/uploads` or `private_uploads` was not on a persistent volume.

### Other

- **Everyone is logged out after each restart**, or logins "jump" between pages: `FLASK_SECRET_KEY` is
  not set. The log shows `WARNING: FLASK_SECRET_KEY is not set`.
- **Links in emails start with `http://`** or login limits block everyone at once: set
  `TRUSTED_PROXY_COUNT=1` behind Caddy/nginx.
- **HTTPS certificate not issued:** the DNS A record must point at the server, and ports 80 and 443
  must be open. See `docker compose logs caddy`.
- **Admin locked out of two-step login:** `docker compose exec web flask --app app reset-2fa USERNAME`.
- **Customers cannot check out:** at least one option in **Admin → Delivery** must have
  "Show at checkout" ticked.

---

## 9. Useful commands

```bash
docker compose ps                                        # are the containers up and healthy?
docker compose logs --tail 100 web                       # recent shop logs
docker compose restart web                               # restart the shop
docker compose exec web flask --app app init-db          # apply database changes now
docker compose exec web flask --app app create-admin     # add an admin
docker compose exec web flask --app app reset-2fa USERNAME
docker compose exec db psql -U shop                      # database console
docker compose down                                      # stop everything (data stays in volumes)
```
