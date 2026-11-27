"""Shared test setup: every test gets a fresh app with its own empty database.

Runs on SQLite by default. To run the same tests on PostgreSQL:
    TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/shop_test pytest
(the test database is wiped before every test).
"""
import io
import os

import pytest
from werkzeug.security import generate_password_hash

from shop import create_app
from shop.db import direct_connection

PG_URL = os.environ.get('TEST_DATABASE_URL', '')


@pytest.fixture
def app(tmp_path):
    if PG_URL:
        c = direct_connection(PG_URL)
        c.execute('DROP SCHEMA public CASCADE'); c.execute('CREATE SCHEMA public'); c.commit(); c.close()
    app = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret',
        'DATABASE_PATH': str(tmp_path / 'test.db'),
        'DATABASE_URL': PG_URL,
        # email is "sent" into the test outbox instead of a real server
        'MAIL_USERNAME': 'shop@test', 'MAIL_PASSWORD': 'x', 'MAIL_DEFAULT_SENDER': 'shop@test', 'MAIL_SUPPRESS_SEND': True,
    })
    seed(app)
    yield app


def connect(app):
    """Connection for test checks; rows come back as plain tuples so they compare easily."""
    c = direct_connection(app.config['DATABASE_URL'], app.config['DATABASE_PATH'])
    if c.dialect == 'sqlite':
        c.raw.row_factory = None
    else:
        from psycopg.rows import tuple_row
        c.raw.row_factory = tuple_row
    return c


def seed(app):
    c = connect(app)
    c.execute("INSERT INTO users(username,email,password_hash,is_admin,confirmed) VALUES('admin','admin@example.com',?,1,1)",
              (generate_password_hash('adminpass'),))
    c.execute("INSERT INTO users(username,email,password_hash,is_admin,confirmed) VALUES('jane','Jane@Example.com',?,0,1)",
              (generate_password_hash('janepass'),))
    products = [("Wireless Headphones", "Over-ear Bluetooth headphones.", "Electronics", 12, 4999, 6500),
                ("Smartphone X2", "6.5 inch display, 128GB storage.", "Electronics", 3, 24999, None),
                ("Cotton T-Shirt", "Soft 100% cotton crew-neck tee.", "Fashion & Wear", 40, 899, None),
                ("Running Shoes", "Lightweight running shoes.", "Fashion & Wear", 0, 3499, 4200),
                ("Electric Kettle", "1.7L stainless steel kettle.", "Home & Kitchen", 8, 2199, None),
                ("Blender Pro", "600W blender.", "Home & Kitchen", 2, 3899, None),
                ("Novel: The River", "Bestselling fiction paperback.", "Books", 25, 650, None)]
    c.executemany("INSERT INTO products(name,description,category,stock,price,original_price) VALUES(?,?,?,?,?,?)", products)
    c.executemany("INSERT INTO products(name,description,category,stock,price) VALUES(?,?,?,?,?)",
                  [(f"Notebook A5 #{i + 1}", "Ruled notebook, 120 pages.", "Office & School", 30, 150 + i) for i in range(12)])
    c.commit()
    c.close()


@pytest.fixture
def db(app):
    """db() opens a connection to the test database, for checking what the app saved."""
    opened = []

    def _connect():
        c = connect(app)
        opened.append(c)
        return c
    yield _connect
    for c in opened:
        c.close()


@pytest.fixture
def login(app):
    """login('jane', 'janepass') -> a logged-in test client."""
    def _login(username, password):
        client = app.test_client()
        client.post('/login', data={'login_identifier': username, 'password': password})
        return client
    return _login


@pytest.fixture
def checkout_details(db):
    """Valid contact/delivery fields for a checkout form (pickup, so no address needed)."""
    pickup = db().execute("SELECT id FROM delivery_zones WHERE kind='pickup'").fetchone()[0]
    return {'contact_name': 'Test Buyer', 'contact_phone': '0712345678', 'delivery_zone': str(pickup)}


def fake_png():
    return io.BytesIO(b'\x89PNG\r\n\x1a\n' + os.urandom(64))
