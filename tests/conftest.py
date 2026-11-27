"""Shared test setup: every test gets a fresh app with its own temporary database."""
import io
import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from shop import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret',
        'DATABASE_PATH': str(tmp_path / 'test.db'),
        # email is "sent" into the test outbox instead of a real server
        'MAIL_USERNAME': 'shop@test', 'MAIL_PASSWORD': 'x', 'MAIL_DEFAULT_SENDER': 'shop@test', 'MAIL_SUPPRESS_SEND': True,
    })
    seed(app.config['DATABASE_PATH'])
    yield app


def seed(path):
    c = sqlite3.connect(path)
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
    """Open a direct connection to the test database (for checking what the app saved)."""
    def connect():
        return sqlite3.connect(app.config['DATABASE_PATH'])
    return connect


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
