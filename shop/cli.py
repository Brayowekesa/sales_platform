"""Command-line tasks:

    flask --app app init-db                       create/upgrade the database
    flask --app app create-admin                  add an admin account
    flask --app app copy-to-postgres sales.db     move an existing SQLite shop into PostgreSQL (DATABASE_URL)
    flask --app app reset-2fa USERNAME            turn off two-step login for someone who lost their phone
"""
import os
import re

import click
from flask import current_app
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from shop.db import IntegrityError, direct_connection, get_db_connection, init_db, schema_version
from shop.security import log_activity


@click.command('init-db')
@with_appcontext
def init_db_command():
    """Create the tables, or upgrade an existing database to the latest version."""
    init_db()
    with get_db_connection() as conn:
        click.echo(f'Database ready ({conn.dialect}, schema version {schema_version(conn)}).')


@click.command('create-admin')
@with_appcontext
def create_admin_command():
    """Create an admin account (the password is not shown while you type)."""
    username = click.prompt('Admin username').strip()
    if not re.match(r'^[A-Za-z0-9._-]{4,25}$', username):
        raise click.ClickException('Username: 4-25 letters, numbers, dots, dashes or underscores.')
    email = click.prompt('Admin email').strip().lower()
    password = click.prompt('Admin password', hide_input=True, confirmation_prompt=True)
    if len(password) < 8:
        raise click.ClickException('Use a password of at least 8 characters.')
    try:
        with get_db_connection() as conn:
            conn.execute('INSERT INTO users (username, email, password_hash, is_admin, confirmed, created_at) '
                         'VALUES (?, ?, ?, 1, 1, CURRENT_TIMESTAMP)', (username, email, generate_password_hash(password)))
    except IntegrityError:
        raise click.ClickException(f"An account with username '{username}' or email '{email}' already exists.")
    log_activity('Admin user created via CLI', details=f'Username: {username}')
    click.echo(f"Admin user '{username}' created.")


@click.command('reset-2fa')
@click.argument('username')
@with_appcontext
def reset_2fa_command(username):
    """Turn off two-step login for USERNAME (e.g. an admin who lost their phone)."""
    with get_db_connection() as conn:
        cur = conn.execute('UPDATE users SET totp_secret = NULL, totp_enabled = 0, recovery_codes = NULL WHERE username = ?', (username,))
    if not cur.rowcount:
        raise click.ClickException(f"No user called '{username}'.")
    log_activity('Two-step login reset via CLI', details=f'Username: {username}')
    click.echo(f"Two-step login turned off for '{username}'. They should turn it on again after logging in.")


# Parents before children, so foreign keys are satisfied while copying
COPY_ORDER = ['users', 'products', 'promotions', 'delivery_zones', 'settings', 'orders', 'order_items',
              'order_status_history', 'reviews', 'wishlists', 'carts', 'product_images', 'activity_logs']


@click.command('copy-to-postgres')
@click.argument('sqlite_file', type=click.Path(exists=True, dir_okay=False))
@with_appcontext
def copy_to_postgres_command(sqlite_file):
    """Copy all shop data from a SQLite file into the PostgreSQL database in DATABASE_URL.

    The PostgreSQL database must be new (no customers or orders yet).
    """
    url = current_app.config.get('DATABASE_URL', '')
    if not url.startswith(('postgres://', 'postgresql://')):
        raise click.ClickException('Set DATABASE_URL to your PostgreSQL database first.')
    init_db()                                            # target gets the latest schema
    src = direct_connection(database_path=os.path.abspath(sqlite_file))
    dst = direct_connection(url)
    try:
        # bring an old SQLite file up to date first, so the columns match
        from shop.db import _upgrade_old_sqlite
        _upgrade_old_sqlite(src); src.commit()

        if dst.execute('SELECT COUNT(*) FROM users').fetchone()[0] or dst.execute('SELECT COUNT(*) FROM orders').fetchone()[0]:
            raise click.ClickException('The PostgreSQL database already has users or orders - refusing to overwrite it.')
        src_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        # Keep the default delivery options unless the old shop has its own
        for table in ('delivery_zones', 'settings'):
            if table in src_tables and src.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                dst.execute(f'DELETE FROM {table}')
            else:
                src_tables.discard(table)
        for table in COPY_ORDER:
            if table not in src_tables:
                continue
            dst_cols = [r[0] for r in dst.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position', (table,)).fetchall()]
            src_cols = [r[1] for r in src.execute(f'PRAGMA table_info({table})').fetchall()]
            cols = [c for c in src_cols if c in dst_cols]
            rows = [tuple(r[c] for c in cols) for r in src.execute(f'SELECT {", ".join(cols)} FROM {table}').fetchall()]
            if rows:
                placeholders = ', '.join('?' for _ in cols)
                cur = dst.raw.cursor()
                cur.executemany(f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({placeholders})'.replace('?', '%s'), rows)
            if 'id' in cols:   # continue numbering after the copied ids
                dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
            click.echo(f'  {table}: {len(rows)} rows')
        # Copied products start their price history today (honest-price rule)
        dst.execute('''INSERT INTO price_history (product_id, price, original_price)
                       SELECT id, price, original_price FROM products p
                       WHERE NOT EXISTS (SELECT 1 FROM price_history h WHERE h.product_id = p.id)''')
        dst.commit()
        click.echo('Done. Uploaded photos live in static/uploads - copy that folder to the new server too.')
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()
