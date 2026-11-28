"""Database layer: SQL translation, upgrading old databases, copying SQLite -> PostgreSQL."""
import sqlite3

import pytest

from conftest import PG_URL
from shop import create_app
from shop.db import _to_postgres, direct_connection

# The schema the shop had before migrations existed (simplified: the tables the upgrade touches)
OLD_SCHEMA = '''
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0, confirmed INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT, image TEXT DEFAULT 'uploads/default.jpg',
                       category TEXT DEFAULT 'Uncategorized', stock INTEGER DEFAULT 0, price REAL NOT NULL, original_price REAL,
                       sale_start_date DATETIME, sale_end_date DATETIME, is_on_sale INTEGER DEFAULT 0, added_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, order_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                     total_amount REAL NOT NULL, status TEXT DEFAULT 'Pending', payment_method TEXT, shipping_address TEXT, transaction_id TEXT);
CREATE TABLE order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
                          quantity INTEGER NOT NULL, price REAL NOT NULL);
INSERT INTO users (username, email, password_hash, confirmed) VALUES ('mary', 'mary@x.ke', 'x', 1);
INSERT INTO products (name, price, stock) VALUES ('Old Radio', 2999, 2);
INSERT INTO orders (user_id, total_amount, status, shipping_address) VALUES (1, 2999, 'Processing', 'Kisumu');
INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (1, 1, 1, 2999);
'''


def test_sql_translation_for_postgres():
    assert _to_postgres('SELECT * FROM t WHERE a = ? AND b = ?') == 'SELECT * FROM t WHERE a = %s AND b = %s'
    assert _to_postgres("SELECT '?' , x LIKE ?") == "SELECT '?' , x LIKE %s"          # ? inside quotes is kept
    assert _to_postgres("SELECT '50%' WHERE p > 10 % 3") == "SELECT '50%%' WHERE p > 10 %% 3"


def test_old_sqlite_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / 'old.db'
    c = sqlite3.connect(path); c.executescript(OLD_SCHEMA); c.commit(); c.close()
    create_app({'TESTING': True, 'SECRET_KEY': 'x', 'DATABASE_PATH': str(path), 'DATABASE_URL': ''})
    c = sqlite3.connect(path)
    assert c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] >= 1
    assert {'phone', 'verification_attempts'} <= {r[1] for r in c.execute('PRAGMA table_info(users)')}
    assert {'delivery_fee', 'contact_phone'} <= {r[1] for r in c.execute('PRAGMA table_info(orders)')}
    assert c.execute('SELECT COUNT(*) FROM delivery_zones').fetchone()[0] == 5
    assert c.execute("SELECT shipping_address FROM orders").fetchone()[0] == 'Kisumu'      # data kept


@pytest.mark.skipif(not PG_URL, reason='needs TEST_DATABASE_URL (PostgreSQL)')
def test_copy_sqlite_shop_into_postgres(tmp_path):
    path = tmp_path / 'old.db'
    c = sqlite3.connect(path); c.executescript(OLD_SCHEMA); c.commit(); c.close()
    pg = direct_connection(PG_URL); pg.execute('DROP SCHEMA public CASCADE'); pg.execute('CREATE SCHEMA public'); pg.commit(); pg.close()
    app = create_app({'TESTING': True, 'SECRET_KEY': 'x', 'DATABASE_URL': PG_URL, 'DATABASE_PATH': str(tmp_path / 'unused.db')})
    result = app.test_cli_runner().invoke(args=['copy-to-postgres', str(path)])
    assert result.exit_code == 0, result.output
    pg = direct_connection(PG_URL)
    assert pg.execute('SELECT username FROM users').fetchone()[0] == 'mary'
    assert pg.execute('SELECT COUNT(*) FROM delivery_zones').fetchone()[0] == 5               # defaults kept
    new_id = pg.execute("INSERT INTO products (name, price) VALUES ('New', 1)").lastrowid
    assert new_id == 2                                                                          # numbering continues
    pg.rollback(); pg.close()
    again = app.test_cli_runner().invoke(args=['copy-to-postgres', str(path)])
    assert again.exit_code != 0 and 'refusing to overwrite' in again.output
