# ESA E-Shop

An online shop for Kenya, built with Python and Flask. Customers browse on their phones, pay
with **M-Pesa** (STK push) or **cash on delivery**, and get their parcel from a rider or a pickup point.
Staff run everything from the admin panel at `/admin`.

## What makes it different

| Feature | What it does |
|---|---|
| **Delivery PIN + rider link** | When an order is marked *Shipped*, the customer gets a 4-digit PIN. The rider opens a link (no account needed), sees the address, and enters the PIN at handover. The order then becomes *Delivered*. |
| **Packing proof** | Staff upload photos or a short video of the exact item while packing. The customer taps *Looks right* or *Something's wrong* before it is sent. |
| **Honest prices** | A crossed-out "was" price only shows if the product really sold at that price for 14 days in the last 90 days, and for at most 60 days. Product pages show a 90-day price history. |
| **Price-drop and back-in-stock alerts** | Customers with the product in their wishlist get an email (and SMS, if switched on) when the price drops or it comes back in stock. |
| **Data saver and small photos** | Photos are served as resized WebP copies (location data removed). Data-saver mode uses even smaller photos and skips the web font. |
| **WhatsApp buttons** | "Ask on WhatsApp" on product pages, a chat button on every page, and WhatsApp links for staff on the order page. |
| **Landmark + map pin** | At checkout the customer can add a landmark ("green gate opposite the petrol station") and their phone's GPS location. The rider gets an *Open in Google Maps* button. |
| **Installable app + offline page** | The shop can be added to the phone's home screen. With no network it shows a friendly offline page. |
| **Tuma kwa Mama (gift orders)** | Send an order to someone else. The receiver gets SMS updates and a tracking link without prices (except the amount to pay for cash on delivery). |
| **M-Pesa + cash on delivery** | STK push to the customer's phone, with a protected callback. Or pay when the parcel arrives. |
| **Delivery zones and pickup points** | Each zone has its own fee, optional free-delivery amount and delivery time. Pickup points have no address form. |
| **Admin two-step login** | Authenticator-app codes and recovery codes. On the Docker setup admins must turn it on. |

Also included: product photo galleries, categories, search, reviews, wishlist, order tracking,
email updates, optional SMS via Africa's Talking, promotions banner, reports and an activity log.

## Tech stack

- Python 3 with Flask 3, Flask-Login, Flask-WTF, Flask-Mail
- SQLite on your own computer, PostgreSQL on the live site (psycopg 3)
- Pillow for resized WebP photos, pyotp + qrcode for two-step login
- Bootstrap 4 templates (Jinja), a small service worker for the installable app
- Gunicorn + Caddy (automatic HTTPS) in Docker
- Optional: Sentry for error reports, Africa's Talking for SMS
- pytest test suite; GitHub Actions runs it on SQLite and PostgreSQL

## Quick start (your own computer)

You need Python 3.10 or newer (Docker and the automatic checks use 3.12).

1. Make a virtual environment and turn it on:

   ```bash
   python -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   ```

2. Install the packages:

   ```bash
   pip install -r requirements.txt
   ```

3. Make your settings file and edit it:

   ```bash
   cp .env.example .env            # Windows: copy .env.example .env
   ```

   For local work you can leave most of it empty. Leave `DATABASE_URL` empty to use the
   SQLite file `sales.db`. Without email settings, emails are not sent. In debug mode the
   verification code is written to the terminal when the email fails.

4. Create the database and your admin account:

   ```bash
   flask --app app init-db
   flask --app app create-admin
   ```

   `create-admin` asks for a username (4-25 letters, numbers, `.` `_` `-`), an email and a
   password (at least 8 characters).

5. Start the shop:

   ```bash
   flask --app app run --debug
   ```

   Open http://127.0.0.1:5000 for the shop and http://127.0.0.1:5000/admin for the admin panel.

Never use `--debug` or `FLASK_DEBUG=1` on the live site.

### Command-line tasks

| Command | What it does |
|---|---|
| `flask --app app init-db` | Create the tables, or upgrade the database to the latest version. Safe to run again. |
| `flask --app app create-admin` | Add an admin account. |
| `flask --app app reset-2fa USERNAME` | Turn off two-step login for someone who lost their phone and recovery codes. |
| `flask --app app copy-to-postgres sales.db` | Copy an old SQLite shop into the PostgreSQL database in `DATABASE_URL`. |

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use a fresh, empty SQLite database for each test. To run the same tests on PostgreSQL,
point `TEST_DATABASE_URL` at an **empty test database**. It is wiped before every test, so never
use your real shop database:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/shop_test pytest
```

The automatic checks (`.github/workflows/checks.yml`) also run `python -m pyflakes shop tests app.py`,
a vulnerability scan of `requirements.txt` (`pip-audit`) and a Docker build.

## Putting it online (Docker)

The live setup is three containers, described in `docker-compose.yml`:

| Container | What it is |
|---|---|
| `web` | The shop, run by Gunicorn on port 8000 (`Dockerfile`, `docker/gunicorn.conf.py`). On start, `docker/entrypoint.sh` runs `flask --app app init-db` once, then starts Gunicorn. |
| `db` | PostgreSQL 16. Data is kept in the `pgdata` volume. |
| `caddy` | The front door on ports 80 and 443. It gets free HTTPS certificates for `DOMAIN` and forwards to `web` (`deploy/Caddyfile`). |

Product photos (`uploads` volume) and packing proofs (`private_uploads` volume) survive updates.
The compose file sets `REQUIRE_ADMIN_2FA=1`, `SESSION_COOKIE_SECURE=1`, `TRUSTED_PROXY_COUNT=1`
and `AUTO_MIGRATE=0` for you.

In short:

```bash
cp .env.example .env        # fill in DOMAIN, POSTGRES_PASSWORD, FLASK_SECRET_KEY, email, M-Pesa
docker compose up -d --build
docker compose exec web flask --app app create-admin
```

Backups: `sh deploy/backup.sh`. Restore: `sh deploy/restore.sh`.

The full step-by-step guide, all settings, backups, updates and troubleshooting are in
**[docs/OPERATIONS.md](docs/OPERATIONS.md)**. [DEPLOY.md](DEPLOY.md) is a shorter version of the same setup.

## Project layout

```
app.py                  entry point: flask --app app run (local) / gunicorn app:app (Docker)
shop/
  __init__.py           create_app(): settings, extensions, pages, CLI commands
  config.py             settings read from environment variables (.env)
  db.py                 database (SQLite or PostgreSQL) and numbered migrations
  cli.py                init-db, create-admin, reset-2fa, copy-to-postgres
  payments.py           M-Pesa STK push, stock, order statuses, delivery PIN check
  delivery.py           packing-proof uploads, delivery PIN, rider links
  gifts.py              Tuma kwa Mama: receiver SMS and tracking links
  pricing.py            honest prices, price history, wishlist alerts
  images.py             resized WebP copies and data saver
  notifications.py      emails and SMS (Africa's Talking)
  security.py           rate limits, upload checks, security headers, activity log
  helpers.py            money/dates, product photos, shop settings, delivery fees
  forms.py, models.py   web forms and the logged-in user
  views/                customer pages: store, cart, checkout and orders, login, account, info pages
  admin/                admin panel (/admin): dashboard, products, orders, members, delivery, settings
templates/              HTML (Jinja); admin/ for the admin panel, emails/ for email bodies
static/                 CSS, JavaScript (incl. the service worker sw.js), logos and icons
static/uploads/         product and promotion photos (not in git)
private_uploads/        packing-proof photos/videos - not public (not in git)
tests/                  pytest suite
docker/                 entrypoint.sh and Gunicorn settings
deploy/                 Caddyfile, backup.sh, restore.sh
```

## Guides

- [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) - for shop staff: orders, packing proof, riders, products, settings
- [docs/OPERATIONS.md](docs/OPERATIONS.md) - for whoever runs the server: settings, deployment, backups, security, troubleshooting
- [DEPLOY.md](DEPLOY.md) - short deployment guide

## Keep these out of git

`.env`, `sales.db` (or any other database file), `backups/`, `static/uploads/` and
`private_uploads/` hold passwords or customer data. These are listed in `.gitignore`; if you
use a different database file name, add it there too.
