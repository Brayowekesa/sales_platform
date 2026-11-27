"""Database access for SQLite (development) and PostgreSQL (production).

Set DATABASE_URL=postgresql://user:password@host:5432/dbname to use PostgreSQL;
without it the app uses the SQLite file in DATABASE_PATH.

The rest of the app writes SQLite-style SQL with ? placeholders; on PostgreSQL the
Connection wrapper below translates it, so views don't need to care which database is used.
"""
import atexit
import re
import sqlite3

from flask import current_app

try:
    import psycopg
    from psycopg_pool import ConnectionPool
except ImportError:  # PostgreSQL support is optional for local development
    psycopg = None
    ConnectionPool = None

# Catch these instead of sqlite3.* so the code works on both databases
IntegrityError = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())
DatabaseError = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())

# Tables without an "id" column (INSERTs into these don't return a new id)
_NO_ID_TABLES = {'settings', 'schema_version'}
_pools = {}


@atexit.register
def _close_pools():
    for pool in _pools.values():
        pool.close(timeout=1)


# ---------------------------------------------------------------- connections
class Row(dict):
    """A result row usable like sqlite3.Row: row['name'], row[0] and dict(row)."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, 'lastrowid', None)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor.fetchall())


def _to_postgres(sql):
    """? placeholders -> %s (outside quoted strings); literal % -> %%."""
    out, quote = [], None
    for ch in sql:
        if quote:
            out.append('%%' if ch == '%' else ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == '?':
            out.append('%s')
        elif ch == '%':
            out.append('%%')
        else:
            out.append(ch)
    return ''.join(out)


class Connection:
    """Same interface for both databases. Use as `with get_db_connection() as conn:` -
    commits on success, rolls back on error, then closes."""

    def __init__(self, raw, dialect, pool=None):
        self.raw, self.dialect, self._pool, self._closed = raw, dialect, pool, False

    def execute(self, sql, params=()):
        if self.dialect == 'sqlite':
            return Cursor(self.raw.execute(sql, params))
        sql = _to_postgres(sql)
        returning = False
        m = re.match(r'\s*INSERT\s+INTO\s+(\w+)', sql, re.I)
        if m and m.group(1).lower() not in _NO_ID_TABLES and 'RETURNING' not in sql.upper():
            sql += ' RETURNING id'
            returning = True
        cur = self.raw.cursor()
        cur.execute(sql, params)
        lastrowid = None
        if returning:
            row = cur.fetchone()
            lastrowid = row['id'] if isinstance(row, dict) else row[0]
        return Cursor(cur, lastrowid)

    def executemany(self, sql, seq):
        if self.dialect == 'sqlite':
            return self.raw.executemany(sql, seq)
        cur = self.raw.cursor()
        cur.executemany(_to_postgres(sql), seq)
        return Cursor(cur)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            self._pool.putconn(self.raw)
        else:
            self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def direct_connection(database_url='', database_path=None):
    """A plain (not pooled) connection - for scripts and tests. Remember to close it."""
    if database_url.startswith(('postgres://', 'postgresql://')):
        raw = psycopg.connect(database_url.replace('postgres://', 'postgresql://', 1), row_factory=_row_factory, options='-c TimeZone=UTC')
        return Connection(raw, 'postgres')
    conn = sqlite3.connect(database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return Connection(conn, 'sqlite')


def database_url():
    return current_app.config.get('DATABASE_URL') or ''


def get_db_connection():
    url = database_url()
    if url.startswith(('postgres://', 'postgresql://')):
        if psycopg is None:
            raise RuntimeError('DATABASE_URL points to PostgreSQL but psycopg is not installed: pip install "psycopg[binary,pool]"')
        pool = _pools.get(url)
        if pool is None:
            pool = _pools[url] = ConnectionPool(
                url.replace('postgres://', 'postgresql://', 1), min_size=1, max_size=int(current_app.config.get('DB_POOL_SIZE', 10)),
                kwargs={'row_factory': _row_factory, 'options': '-c TimeZone=UTC'}, open=True)
        raw = pool.getconn()
        return Connection(raw, 'postgres', pool)
    conn = sqlite3.connect(current_app.config['DATABASE_PATH'], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return Connection(conn, 'sqlite')


# ---------------------------------------------------------------- schema
# Written once; {ID}, {TS} and {MONEY} become the right types for each database.
_TYPES = {
    'sqlite': {'ID': 'INTEGER PRIMARY KEY AUTOINCREMENT', 'TS': 'DATETIME', 'MONEY': 'REAL'},
    'postgres': {'ID': 'SERIAL PRIMARY KEY', 'TS': 'TIMESTAMP', 'MONEY': 'DOUBLE PRECISION'},
}

SCHEMA_V1 = [
    '''CREATE TABLE IF NOT EXISTS users (
        id {ID},
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        confirmed INTEGER DEFAULT 0,
        verification_code TEXT,
        verification_code_expires TEXT,          -- 'YYYY-MM-DD HH:MM:SS' (UTC)
        verification_attempts INTEGER DEFAULT 0,
        phone TEXT,                              -- remembered for the next checkout
        created_at {TS} DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS promotions (
        id {ID},
        title TEXT NOT NULL,
        image_path TEXT,
        target_url TEXT,
        alt_text TEXT,
        original_price {MONEY},
        promo_price {MONEY},
        discount_text TEXT,
        is_active INTEGER DEFAULT 1,
        display_order INTEGER DEFAULT 0,
        created_at {TS} DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS products (
        id {ID},
        name TEXT NOT NULL,
        description TEXT,
        image TEXT DEFAULT 'uploads/default.jpg',
        category TEXT DEFAULT 'Uncategorized',
        stock INTEGER DEFAULT 0,
        price {MONEY} NOT NULL,
        original_price {MONEY},
        sale_start_date {TS},
        sale_end_date {TS},
        is_on_sale INTEGER DEFAULT 0,
        added_at {TS} DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS carts (
        id {ID},
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        quantity INTEGER NOT NULL,
        added_at {TS} DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, product_id))''',
    '''CREATE TABLE IF NOT EXISTS orders (
        id {ID},
        user_id INTEGER NOT NULL REFERENCES users(id),
        order_date {TS} DEFAULT CURRENT_TIMESTAMP,
        subtotal {MONEY},
        delivery_fee {MONEY} DEFAULT 0,
        total_amount {MONEY} NOT NULL,
        status TEXT DEFAULT 'Pending',
        payment_method TEXT,
        shipping_address TEXT,
        transaction_id TEXT,
        contact_name TEXT,
        contact_phone TEXT,
        delivery_method TEXT,
        delivery_zone TEXT)''',
    '''CREATE TABLE IF NOT EXISTS order_items (
        id {ID},
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity INTEGER NOT NULL,
        price {MONEY} NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS reviews (
        id {ID},
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        rating INTEGER CHECK (rating BETWEEN 1 AND 5),
        comment TEXT,
        created_at {TS} DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, product_id))''',
    '''CREATE TABLE IF NOT EXISTS wishlists (
        id {ID},
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        added_at {TS} DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, product_id))''',
    '''CREATE TABLE IF NOT EXISTS activity_logs (
        id {ID},
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        details TEXT,
        timestamp {TS} DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS rate_limit_events (
        id {ID},
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        created_at {TS} DEFAULT CURRENT_TIMESTAMP)''',
    'CREATE INDEX IF NOT EXISTS idx_rate_limit ON rate_limit_events(kind, key, created_at)',
    '''CREATE TABLE IF NOT EXISTS delivery_zones (
        id {ID},
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'delivery',   -- 'delivery' or 'pickup'
        fee {MONEY} NOT NULL DEFAULT 0,
        free_over {MONEY},                       -- free delivery when the subtotal reaches this
        eta TEXT,
        details TEXT,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0)''',
    'CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)',
    '''CREATE TABLE IF NOT EXISTS order_status_history (
        id {ID},
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        status TEXT NOT NULL,
        note TEXT,
        created_at {TS} DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS product_images (
        id {ID},
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        sort_order INTEGER DEFAULT 0)''',
    'CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)',
    'CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)',
    'CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)',
]

DEFAULT_DELIVERY_ZONES = [
    ('Nairobi CBD', 'delivery', 150, 3000, 'Same day', 'Orders placed before 2 pm arrive the same day.', 0),
    ('Nairobi suburbs', 'delivery', 250, 3000, 'Same or next day', 'Westlands, Kilimani, Kileleshwa, South B/C, Eastlands, Kasarani and nearby.', 1),
    ('Greater Nairobi', 'delivery', 400, 5000, '1-2 days', 'Kiambu, Ruiru, Thika Road, Rongai, Kitengela, Syokimau, Athi River.', 2),
    ('Rest of Kenya (courier)', 'delivery', 600, None, '2-4 days', 'Sent by courier to your nearest town office.', 3),
    ('Pick up from our shop', 'pickup', 0, None, 'Ready within 24 hours', 'Set your shop address and opening hours in Admin -> Delivery.', 4),
]


def _migration_1_initial_schema(conn):
    for statement in SCHEMA_V1:
        conn.execute(statement.format(**_TYPES[conn.dialect]))
    if conn.execute('SELECT COUNT(*) FROM delivery_zones').fetchone()[0] == 0:
        conn.executemany('INSERT INTO delivery_zones (name, kind, fee, free_over, eta, details, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         DEFAULT_DELIVERY_ZONES)


# Add new schema changes at the end as (version, description, function). Never edit old ones.
MIGRATIONS = [
    (1, 'initial schema', _migration_1_initial_schema),
]


def _sqlite_columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]


def _upgrade_old_sqlite(conn):
    """Bring a SQLite database made by older versions of the shop (before migrations) up to schema 1."""
    users = _sqlite_columns(conn, 'users')
    for col, ddl in [('verification_code', 'TEXT'), ('verification_code_expires', 'TEXT'),
                     ('verification_attempts', 'INTEGER DEFAULT 0'), ('phone', 'TEXT')]:
        if col not in users:
            conn.execute(f'ALTER TABLE users ADD COLUMN {col} {ddl}')
    orders = _sqlite_columns(conn, 'orders')
    for col, ddl in [('contact_name', 'TEXT'), ('contact_phone', 'TEXT'), ('delivery_method', 'TEXT'),
                     ('delivery_zone', 'TEXT'), ('delivery_fee', 'REAL DEFAULT 0'), ('subtotal', 'REAL')]:
        if col not in orders:
            conn.execute(f'ALTER TABLE orders ADD COLUMN {col} {ddl}')


def schema_version(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)')
    row = conn.execute('SELECT MAX(version) FROM schema_version').fetchone()
    return row[0] or 0


def init_db():
    """Create or upgrade the database to the latest schema. Safe to run on every start."""
    with get_db_connection() as conn:
        if conn.dialect == 'sqlite':
            conn.execute('PRAGMA journal_mode=WAL')
        version = schema_version(conn)
        if version == 0 and conn.dialect == 'sqlite' and 'users' in {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            _upgrade_old_sqlite(conn)
        for number, description, migrate in MIGRATIONS:
            if number > version:
                migrate(conn)
                conn.execute('INSERT INTO schema_version (version) VALUES (?)', (number,))
                current_app.logger.info(f'Database migrated to version {number}: {description}')
