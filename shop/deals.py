"""Pamoja deals: buy together, pay less.

A deal has price steps ("tiers") by number of buyers, e.g. 1 buyer KSh 2,500, 5 buyers KSh 2,100,
10 buyers KSh 1,900. Everyone who joins pays the lowest price reached when the deal ends:
- cash on delivery orders simply get a lower total;
- orders already paid by M-Pesa get `deal_refund_due` set, and staff send the difference back.
Deal orders are not shipped until the deal has ended (the final price must be known first).

Times are stored in UTC ('YYYY-MM-DD HH:MM:SS'), like the rest of the database.
"""
import json
from datetime import datetime, timedelta, timezone

from flask import current_app

from shop.db import get_db_connection

# Orders in these states don't count as buyers (and don't get the final price)
NOT_COUNTED = ('Cancelled', 'Payment Failed', 'Refunded')
MAX_TIERS = 5


class TierError(ValueError):
    pass


def utc_now_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def as_utc(value):
    """A database time (string or datetime, stored as UTC) as an aware datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.strptime(value.split('.')[0], '%Y-%m-%d %H:%M:%S')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_tiers(text, normal_price=None):
    """'1:2500, 5:2100, 10:1900' -> [(1, 2500.0), (5, 2100.0), (10, 1900.0)].

    Rules: starts at 1 buyer, buyers go up, prices go down, at most MAX_TIERS steps,
    and the 1-buyer price can't be above the product's normal price.
    """
    tiers = []
    for part in (text or '').replace('\n', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            buyers, price = part.split(':')
            tiers.append((int(buyers), round(float(price), 2)))
        except ValueError:
            raise TierError(f'"{part}" should look like 5:2100 (buyers:price).')
    if not tiers:
        raise TierError('Add at least one price step, e.g. 1:2500, 5:2100, 10:1900.')
    if len(tiers) > MAX_TIERS:
        raise TierError(f'Use at most {MAX_TIERS} price steps.')
    if tiers[0][0] != 1:
        raise TierError('The first step must be for 1 buyer (the starting price).')
    for (b1, p1), (b2, p2) in zip(tiers, tiers[1:]):
        if b2 <= b1 or p2 >= p1:
            raise TierError('Each step needs more buyers and a lower price than the one before.')
    if any(p <= 0 for _, p in tiers):
        raise TierError('Prices must be above zero.')
    if normal_price is not None and tiers[0][1] > float(normal_price):
        raise TierError('The starting price must not be above the normal price.')
    return tiers


def tiers_text(tiers):
    return ', '.join(f'{b}:{p:g}' for b, p in tiers)


def price_for(tiers, buyers):
    price = tiers[0][1]
    for b, p in tiers:
        if buyers >= b:
            price = p
    return price


def buyer_count(conn, deal_id, exclude_user=None):
    sql = (f"SELECT COUNT(DISTINCT o.user_id) FROM order_items oi JOIN orders o ON o.id = oi.order_id "
           f"WHERE oi.deal_id = ? AND o.status NOT IN ({', '.join('?' for _ in NOT_COUNTED)})")
    params = [deal_id, *NOT_COUNTED]
    if exclude_user is not None:
        sql += ' AND o.user_id <> ?'
        params.append(exclude_user)
    return conn.execute(sql, params).fetchone()[0] or 0


def deal_info(conn, deal, user_id=None):
    """Everything the pages need about a deal (a dict)."""
    d = dict(deal)
    tiers = [tuple(t) for t in json.loads(d['tiers'])]
    buyers = buyer_count(conn, d['id'])
    ends = as_utc(d['ends_at'])
    now = datetime.now(timezone.utc)
    is_open = d['status'] == 'active' and ends > now
    current = d['final_price'] if d['status'] == 'closed' and d['final_price'] is not None else price_for(tiers, buyers)
    # what a new buyer would pay right now (they count themselves)
    joined = bool(user_id) and buyer_count(conn, d['id'], exclude_user=user_id) != buyers
    join_price = price_for(tiers, buyers if joined else buyers + 1)
    nxt = next(((b, p) for b, p in tiers if b > buyers), None)
    top = tiers[-1][0]
    d.update(
        tiers=[{'buyers': b, 'price': p, 'reached': buyers >= b} for b, p in tiers],
        tiers_list=tiers, buyers=buyers, current_price=current, join_price=join_price, joined=joined,
        next_tier={'buyers': nxt[0], 'price': nxt[1], 'needed': nxt[0] - buyers} if nxt else None,
        progress=min(100, round(100 * buyers / top)) if top > 1 else 100,
        is_open=is_open, ends_at_iso=ends.strftime('%Y-%m-%dT%H:%M:%SZ'),
        seconds_left=max(0, int((ends - now).total_seconds())),
        best_price=tiers[-1][1],
    )
    return d


def open_deal_for_product(conn, product_id):
    return conn.execute('''SELECT * FROM group_deals WHERE product_id = ? AND status = 'active' AND ends_at > ?
                           ORDER BY ends_at LIMIT 1''', (product_id, utc_now_str())).fetchone()


def open_deals(conn, limit=None):
    sql = '''SELECT d.*, p.name AS product_name, p.image AS product_image, p.price AS normal_price, p.stock
             FROM group_deals d JOIN products p ON p.id = d.product_id
             WHERE d.status = 'active' AND d.ends_at > ? ORDER BY d.ends_at'''
    if limit:
        sql += f' LIMIT {int(limit)}'
    return conn.execute(sql, (utc_now_str(),)).fetchall()


def deal_cart_price(conn, deal_id, user_id):
    """Price for this user's cart line if the deal is still open, else None (normal price applies)."""
    deal = conn.execute("SELECT * FROM group_deals WHERE id = ? AND status = 'active' AND ends_at > ?",
                        (deal_id, utc_now_str())).fetchone()
    if not deal:
        return None, None
    info = deal_info(conn, deal, user_id)
    return info['join_price'], info


def apply_deal_prices(conn, rows, user_id):
    """Cart rows (with product price and c.deal_id) -> dicts with the deal price where it applies."""
    out = []
    for r in rows:
        item = dict(r)
        item['deal'] = None
        if item.get('deal_id'):
            price, info = deal_cart_price(conn, item['deal_id'], user_id)
            if price is None:
                item['deal_id'] = None                    # deal over: back to the normal price
            else:
                item['normal_price'] = item['price']
                item['price'] = price
                item['deal'] = info
        out.append(item)
    return out


def open_deal_ids_for_order(conn, order_id):
    rows = conn.execute('''SELECT DISTINCT d.id, d.ends_at FROM order_items oi JOIN group_deals d ON d.id = oi.deal_id
                           WHERE oi.order_id = ? AND d.status = 'active' ''', (order_id,)).fetchall()
    return rows


def close_deal(deal_id, who='system'):
    """End a deal: fix the final price and lower every buyer's order to it.

    Returns (final_price, [order ids changed]) or None if the deal was already closed.
    """
    from shop.payments import record_order_status
    changed = []
    conn = get_db_connection()
    with conn:
        cur = conn.execute("UPDATE group_deals SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'active'",
                           (deal_id,))
        if cur.rowcount == 0:
            return None                                  # someone else closed it first
        deal = conn.execute('SELECT * FROM group_deals WHERE id = ?', (deal_id,)).fetchone()
        tiers = [tuple(t) for t in json.loads(deal['tiers'])]
        buyers = buyer_count(conn, deal_id)
        final = price_for(tiers, buyers)
        conn.execute('UPDATE group_deals SET final_price = ?, final_buyers = ? WHERE id = ?', (final, buyers, deal_id))
        items = conn.execute(f'''SELECT oi.id, oi.order_id, oi.quantity, oi.price, o.payment_method, o.status
                                 FROM order_items oi JOIN orders o ON o.id = oi.order_id
                                 WHERE oi.deal_id = ? AND o.status NOT IN ({', '.join('?' for _ in NOT_COUNTED)})''',
                             (deal_id, *NOT_COUNTED)).fetchall()
        for it in items:
            saving = round((it['price'] - final) * it['quantity'], 2)
            if saving > 0:
                conn.execute('UPDATE order_items SET price = ? WHERE id = ?', (final, it['id']))
                conn.execute('UPDATE orders SET subtotal = subtotal - ?, total_amount = total_amount - ? WHERE id = ?',
                             (saving, saving, it['order_id']))
                paid_online = it['payment_method'] == 'Lipa na M-Pesa'
                if paid_online:
                    conn.execute('UPDATE orders SET deal_refund_due = COALESCE(deal_refund_due, 0) + ? WHERE id = ?',
                                 (saving, it['order_id']))
                note = (f"Pamoja deal ended with {buyers} buyers: your price dropped to KSh {final:,.0f} each "
                        f"(KSh {saving:,.0f} less)." + (" We'll send the difference back to your M-Pesa." if paid_online else ''))
            else:
                note = f"Pamoja deal ended with {buyers} buyers at KSh {final:,.0f} each. Your order will now be sent."
            record_order_status(conn, it['order_id'], 'Deal ended', note)
            changed.append(it['order_id'])
    current_app.logger.info(f'Pamoja deal {deal_id} closed by {who}: {buyers} buyers, KSh {final}')
    from shop.notifications import notify_order_event
    for oid in sorted(set(changed)):
        notify_order_event(oid, 'deal_closed')
    return final, sorted(set(changed))


def close_due_deals():
    """Close every deal whose time is up. Called from busy pages and `flask --app app close-deals`."""
    with get_db_connection() as conn:
        due = [r['id'] for r in conn.execute("SELECT id FROM group_deals WHERE status = 'active' AND ends_at <= ?",
                                             (utc_now_str(),)).fetchall()]
    closed = 0
    for deal_id in due:
        try:
            if close_deal(deal_id):
                closed += 1
        except Exception as e:      # never break the page that triggered it
            current_app.logger.error(f'Could not close Pamoja deal {deal_id}: {e}', exc_info=True)
    return closed


def default_end():
    return (datetime.now(timezone.utc) + timedelta(days=3))
