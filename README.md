# ESA E-Shop

An online shop for Kenya, built with Python and Flask:

- Product catalogue with photo galleries, categories, search, reviews and a wishlist
- Cart and checkout with delivery areas, delivery fees and pickup points
- Payment by **M-Pesa** (STK push) or **Cash on Delivery**
- Order tracking, with email updates and optional SMS (Africa's Talking)
- Admin panel: products, promotions, orders, members, delivery options, shop settings, reports
- Security:
  - login limits and email verification
  - CSRF protection and security headers
  - upload checks and admin-only areas
  - a protected M-Pesa callback

## Run it on your computer

Requires Python 3.10 or newer.

```bash
python -m venv venv
```

Activate the environment. On Windows: `venv\Scripts\activate`. On Mac or Linux: `source venv/bin/activate`.

```bash
pip install -r requirements.txt
```

```bash
copy .env.example .env
```

(On Mac or Linux, use `cp` instead of `copy`.) Then edit `.env`.

```bash
flask --app app create-admin
```

```bash
flask --app app run --debug
```

Open http://127.0.0.1:5000. On your computer the shop uses a SQLite file (`sales.db`),
so no database server is needed.

## Tests

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

To run the same tests against PostgreSQL (the test database is wiped before each test):

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/shop_test pytest
```

## Putting it online

See [DEPLOY.md](DEPLOY.md). It covers Docker with PostgreSQL, automatic HTTPS, backups and updates.

## Project layout

```
app.py                  entry point (flask --app app run / gunicorn app:app)
shop/__init__.py        create_app(): settings, extensions, pages
shop/config.py          settings from environment variables (.env)
shop/db.py              database (SQLite or PostgreSQL) and migrations
shop/views/             customer pages: store, cart, orders/checkout, auth, info pages
shop/admin/             admin panel (/admin)
shop/payments.py        M-Pesa, stock, order status
shop/notifications.py   emails and SMS
shop/security.py        rate limits, upload checks, headers, admin access
templates/, static/     HTML, CSS, JavaScript, images
tests/                  pytest suite
deploy/, docker/        Caddy config, backup/restore scripts, Gunicorn settings
```
