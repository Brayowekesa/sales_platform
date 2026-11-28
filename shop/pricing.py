"""Honest prices: price history, verified discounts, price-drop and back-in-stock alerts.

Rule: a crossed-out "was" price is shown only if the product really sold at that price (or
higher) for at least VERIFY_DAYS days in the last LOOKBACK_DAYS days, and a discount stops
showing after MAX_SALE_DAYS (a sale that never ends is just the normal price).
"""
import threading
from datetime import datetime, timedelta, timezone

from flask import current_app, render_template
from flask_mail import Message

from shop.db import get_db_connection
from shop.helpers import public_url

VERIFY_DAYS = 14
LOOKBACK_DAYS = 90
MAX_SALE_DAYS = 60
PRICE_DROP_DAYS = 30     # a "price drop" alert needs a price lower than anything in the last 30 days


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        return datetime.strptime(value.split('.')[0], '%Y-%m-%d %H:%M:%S')
    return None


def history(conn, product_id, days=LOOKBACK_DAYS):
    """Price changes in the last `days` days, plus the change in force at the start of the window."""
    since = (utcnow() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    rows = [dict(r) for r in conn.execute(
        'SELECT price, original_price, changed_at FROM price_history WHERE product_id = ? AND changed_at >= ? ORDER BY changed_at, id',
        (product_id, since)).fetchall()]
    before = conn.execute('SELECT price, original_price, changed_at FROM price_history WHERE product_id = ? AND changed_at < ? '
                          'ORDER BY changed_at DESC, id DESC LIMIT 1', (product_id, since)).fetchone()
    if before:
        rows.insert(0, {**dict(before), 'changed_at': since})
    for r in rows:
        r['changed_at'] = _as_datetime(r['changed_at'])
    return rows


def days_at_or_above(rows, amount, now=None):
    """How many days (in `rows`) the selling price was at least `amount`."""
    now = now or utcnow()
    total = timedelta()
    for i, r in enumerate(rows):
        end = rows[i + 1]['changed_at'] if i + 1 < len(rows) else now
        if r['price'] is not None and float(r['price']) >= float(amount) - 0.005:
            total += max(end - r['changed_at'], timedelta())
    return total.total_seconds() / 86400


def check_was_price(conn, product_id, was_price):
    """(verified, days) for a proposed "was" price, using the history BEFORE the new price takes effect."""
    if not was_price:
        return False, 0
    days = days_at_or_above(history(conn, product_id), was_price)
    return days >= VERIFY_DAYS, days


def sale_started_at(conn, product_id, was_price):
    """When the current sale began: start of the latest unbroken run of prices below `was_price`.

    Worked out from the price history, so changing or re-entering the "was" price can't restart
    the 60-day limit - only selling at the full price again ends a sale.
    """
    started = None
    for r in conn.execute('SELECT price, changed_at FROM price_history WHERE product_id = ? ORDER BY changed_at DESC, id DESC',
                          (product_id,)).fetchall():
        if float(r['price']) >= float(was_price) - 0.005:
            break
        started = r['changed_at']
    started = _as_datetime(started)
    return started.strftime('%Y-%m-%d %H:%M:%S') if started else None


def lowest_recent_price(conn, product_id, days=PRICE_DROP_DAYS):
    """Lowest selling price in the last `days` days (including the price in force at the start)."""
    rows = history(conn, product_id, days)
    return min((float(r['price']) for r in rows if r['price'] is not None), default=None)


def apply_price_change(conn, product_id, old, new_price, new_original, new_stock):
    """Call after saving a product. Records history, (re)checks the discount, and says which alerts to send.

    `old` is the product row before the change (None for a new product).
    """
    price_changed = old is None or float(old['price']) != float(new_price) or (old['original_price'] or None) != (new_original or None)
    # Re-check the discount on every save (an older "was" price may have become provable since)
    verified, _ = check_was_price(conn, product_id, new_original) if new_original and new_original > new_price else (False, 0)
    lowest_before = lowest_recent_price(conn, product_id) if old is not None else None
    if price_changed:
        conn.execute('INSERT INTO price_history (product_id, price, original_price) VALUES (?, ?, ?)',
                     (product_id, new_price, new_original))
    started = (sale_started_at(conn, product_id, new_original) or utcnow().strftime('%Y-%m-%d %H:%M:%S')) if verified else None
    conn.execute('UPDATE products SET was_price_verified = ?, discount_started_at = ? WHERE id = ?',
                 (1 if verified else 0, started, product_id))
    alerts = []
    # Only a real drop: lower than the current price AND lower than anything in the last 30 days
    # (raising the price for a day and dropping it back is not a "price drop")
    if old is not None and float(new_price) < float(old['price']) - 0.005 and \
            (lowest_before is None or float(new_price) < lowest_before - 0.005):
        alerts.append(('price_drop', lowest_before if lowest_before is not None else float(old['price'])))
    if old is not None and (old['stock'] or 0) <= 0 and (new_stock or 0) > 0:
        alerts.append(('back_in_stock', None))
    return alerts


def honest_discount(product):
    """Discount % to show for a product (0 = show no discount). Used by templates."""
    try:
        was, price = product.get('original_price'), product.get('price')
        if not was or not price or float(was) <= float(price) or not product.get('was_price_verified'):
            return 0
        started = _as_datetime(product.get('discount_started_at'))
        if started and utcnow() - started > timedelta(days=MAX_SALE_DAYS):
            return 0
        return int(round((float(was) - float(price)) / float(was) * 100))
    except (TypeError, ValueError, AttributeError):
        return 0


def price_chart(conn, product_id, days=LOOKBACK_DAYS):
    """Points for the product page's price-history chart (step line), or None if the price never changed."""
    rows = history(conn, product_id, days)
    if len(rows) < 2:
        return None
    now = utcnow()
    start = rows[0]['changed_at']
    span = max((now - start).total_seconds(), 1) * 1.05     # room on the right so today's price is visible
    prices = [float(r['price']) for r in rows]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.15 or hi * 0.1 or 1
    lo_axis, hi_axis = max(lo - pad, 0), hi + pad
    W, H = 600, 160

    def x(t):
        return round((t - start).total_seconds() / span * W, 1)

    def y(p):
        return round(H - (p - lo_axis) / (hi_axis - lo_axis) * H, 1)
    pts = []
    for i, r in enumerate(rows):
        end = rows[i + 1]['changed_at'] if i + 1 < len(rows) else now
        end_x = W if i + 1 == len(rows) else x(end)              # the current price runs to the right edge
        pts += [(x(r['changed_at']), y(float(r['price']))), (end_x, y(float(r['price'])))]
    return {'points': ' '.join(f'{a},{b}' for a, b in pts), 'width': W, 'height': H, 'lowest': lo, 'highest': hi,
            'start': start, 'current': prices[-1], 'is_lowest': prices[-1] <= lo + 0.005, 'lo_y': y(lo), 'hi_y': y(hi),
            'now_x': x(now), 'now_y': y(prices[-1])}


def send_wishlist_alerts(product_id, alerts):
    """Email (and SMS) everyone who has this product in their wishlist. At most one alert per product per person per day."""
    if not alerts:
        return
    from shop.notifications import mail_configured, send_sms
    from shop.security import rate_limited, record_rate_event
    from shop.extensions import mail
    with get_db_connection() as conn:
        product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
        people = conn.execute('''SELECT u.id, u.email, u.username, u.phone FROM wishlists w JOIN users u ON u.id = w.user_id
                                 WHERE w.product_id = ?''', (product_id,)).fetchall()
    if not product or not people:
        return
    product = dict(product)
    kind, old_price = alerts[0]
    url = public_url('store.product_details', product_id=product_id)
    site = current_app.config['SITE_NAME']
    if kind == 'price_drop':
        headline = f"Price drop: {product['name']}"
        message = f"Good news - {product['name']} on your wishlist is now {current_app.jinja_env.filters['money'](product['price'])} - lower than any price in the last 30 days (lowest before: {current_app.jinja_env.filters['money'](old_price)})."
    else:
        headline = f"Back in stock: {product['name']}"
        message = f"{product['name']} from your wishlist is back in stock. Get it before it sells out again."
    jobs = []
    for p in people:
        key = f"{p['id']}:{product_id}"
        if rate_limited('wishlist_alert', key, 1, 24 * 60):
            continue
        record_rate_event('wishlist_alert', key)
        html = render_template('emails/wishlist_alert.html', name=p['username'], headline=headline, message=message, url=url, product=product)
        jobs.append((p['email'], p['phone'], html))
    app = current_app._get_current_object()

    def deliver():
        with app.app_context():
            for email, phone, html in jobs:
                if mail_configured() and email:
                    try:
                        mail.send(Message(f'{headline} - {site}', recipients=[email], html=html, body=f'{message}\n\n{url}',
                                          sender=app.config['MAIL_DEFAULT_SENDER']))
                    except Exception as e:
                        app.logger.error(f'Wishlist alert email failed: {e}')
                send_sms(phone, f'{site}: {message} {url}')
    if app.config.get('TESTING'):
        deliver()
    else:
        threading.Thread(target=deliver, daemon=True).start()
