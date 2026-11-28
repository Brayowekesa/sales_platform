"""Pamoja deals: buy together, everyone pays the lowest price reached."""
import json

import pytest
from werkzeug.security import generate_password_hash

import shop.views.orders as orders_view
from shop.deals import TierError, parse_tiers, price_for


def zone_id(db, name):
    return str(db().execute('SELECT id FROM delivery_zones WHERE name=?', (name,)).fetchone()[0])


@pytest.fixture
def deal(db):
    """A deal on the Electric Kettle (id 5, normal price 2,199): 1:2000, 2:1800, 3:1600."""
    c = db()
    c.execute("INSERT INTO group_deals (product_id, title, tiers, max_per_buyer, ends_at) VALUES (5, 'Kettle deal', ?, 2, '2099-01-01 00:00:00')",
              (json.dumps([[1, 2000], [2, 1800], [3, 1600]]),))
    for name in ('amos', 'beth', 'carol'):
        c.execute("INSERT INTO users(username,email,password_hash,confirmed) VALUES(?,?,?,1)", (name, f'{name}@example.com', generate_password_hash('pass1234')))
    c.commit()
    return c.execute('SELECT MAX(id) FROM group_deals').fetchone()[0]


def join_and_order(client, db, deal_id, qty=1, payment='Cash on Delivery'):
    client.post(f'/deals/{deal_id}/join', data={'quantity': qty})
    return client.post('/checkout', data={'contact_name': 'Buyer', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                          'shipping_address': 'Moi Ave', 'payment_method': payment})


def test_tier_rules():
    assert parse_tiers('1:2500, 5:2100, 10:1900', 2600) == [(1, 2500.0), (5, 2100.0), (10, 1900.0)]
    for bad in ('', '2:100', '1:100, 1:90', '1:100, 5:120', '1:abc', '1:3000'):
        with pytest.raises(TierError):
            parse_tiers(bad, 2600)
    tiers = [(1, 2500), (5, 2100), (10, 1900)]
    assert [price_for(tiers, n) for n in (0, 1, 4, 5, 9, 10, 50)] == [2500, 2500, 2500, 2100, 2100, 1900, 1900]


def test_everyone_pays_the_lowest_price_reached(app, login, db, deal):
    amos = login('amos', 'pass1234')
    assert b'Join at KSh 2,000' in amos.get(f'/deals/{deal}').data
    join_and_order(amos, db, deal)
    beth = login('beth', 'pass1234')
    assert b'Join at KSh 1,800' in beth.get(f'/deals/{deal}').data           # 2nd buyer price
    join_and_order(beth, db, deal, qty=2)
    page = app.test_client().get(f'/deals/{deal}').data.decode()
    assert '2</strong> people have joined' in page and '1 more</strong> to unlock KSh 1,600' in page
    # orders are held while the deal runs
    oid = db().execute("SELECT o.id FROM orders o JOIN users u ON u.id=o.user_id WHERE u.username='amos'").fetchone()[0]
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped'})
    assert db().execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()[0] == 'Processing'
    assert b'In a Pamoja deal' in admin.get(f'/admin/orders/{oid}').data

    carol = login('carol', 'pass1234')
    join_and_order(carol, db, deal)                                          # 3rd buyer: 1,600 unlocked
    admin.post(f'/admin/deals/{deal}/end')
    rows = db().execute('''SELECT u.username, oi.price, o.subtotal, o.total_amount FROM orders o JOIN users u ON u.id=o.user_id
                           JOIN order_items oi ON oi.order_id=o.id ORDER BY o.id''').fetchall()
    # everyone at 1,600 (+150 delivery under the free-delivery limit; beth's 2 items were delivered free)
    assert rows == [('amos', 1600, 1600, 1750), ('beth', 1600, 3200, 3200), ('carol', 1600, 1600, 1750)]
    assert db().execute('SELECT status, final_price, final_buyers FROM group_deals WHERE id=?', (deal,)).fetchone() == ('closed', 1600, 3)
    assert b'dropped to KSh 1,600' in amos.get(f'/orders/{oid}').data
    admin.post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped'})
    assert db().execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()[0] == 'Shipped'
    # ended deal can't be joined
    amos.post(f'/deals/{deal}/join')
    assert db().execute("SELECT COUNT(*) FROM carts c JOIN users u ON u.id=c.user_id WHERE u.username='amos'").fetchone()[0] == 0


def test_mpesa_buyers_get_refund_note_and_cancelled_dont_count(app, login, db, deal, monkeypatch):
    monkeypatch.setattr(orders_view, 'lipa_na_mpesa_stk_push', lambda *a: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_9'})
    amos = login('amos', 'pass1234')
    join_and_order(amos, db, deal, payment='Lipa na M-Pesa')
    c = db(); c.execute("UPDATE orders SET status='Paid'"); c.commit()
    beth = login('beth', 'pass1234')
    join_and_order(beth, db, deal)
    carol = login('carol', 'pass1234')
    join_and_order(carol, db, deal)
    c = db(); c.execute("UPDATE orders SET status='Cancelled' WHERE user_id=(SELECT id FROM users WHERE username='carol')"); c.commit()
    login('admin', 'adminpass').post(f'/admin/deals/{deal}/end')
    assert db().execute('SELECT final_price, final_buyers FROM group_deals').fetchone() == (1800, 2)   # carol cancelled
    amos_order = db().execute("SELECT id, deal_refund_due, total_amount FROM orders WHERE user_id=(SELECT id FROM users WHERE username='amos')").fetchone()
    assert amos_order[1:] == (200, 1950)
    assert b'send <strong>KSh 200</strong> back to your M-Pesa' in amos.get(f'/orders/{amos_order[0]}').data
    assert db().execute("SELECT total_amount FROM orders WHERE user_id=(SELECT id FROM users WHERE username='carol')").fetchone()[0] == 1750   # cancelled: unchanged


def test_deal_closes_by_itself_when_time_is_up(app, login, db, deal):
    amos = login('amos', 'pass1234')
    join_and_order(amos, db, deal)
    c = db(); c.execute("UPDATE group_deals SET ends_at='2020-01-01 00:00:00'"); c.commit()
    page = app.test_client().get(f'/deals/{deal}').data
    assert b'This deal has ended' in page
    assert db().execute('SELECT status, final_price FROM group_deals').fetchone() == ('closed', 2000)
    runner = app.test_cli_runner()
    assert 'Closed 0 deal(s)' in runner.invoke(args=['close-deals']).output


def test_max_per_buyer_and_cart_price(login, db, deal):
    amos = login('amos', 'pass1234')
    amos.post(f'/deals/{deal}/join', data={'quantity': 9})                  # capped at 2
    assert db().execute('SELECT quantity, deal_id FROM carts').fetchone() == (2, deal)
    page = amos.get('/cart').data.decode()
    assert 'KSh 2,000 each' in page and 'Pamoja price' in page
    c = db(); c.execute('UPDATE carts SET quantity = 5'); c.commit()
    r = amos.post('/checkout', data={'contact_name': 'A', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                     'shipping_address': 'Moi Ave', 'payment_method': 'Cash on Delivery'})
    assert r.status_code == 302 and '/cart' in r.headers['Location']
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_admin_creates_and_validates_deals(login, db):
    admin = login('admin', 'adminpass')
    bad = admin.post('/admin/deals', data={'product_id': '5', 'tiers': '1:3000', 'ends_at': '2099-01-01T10:00', 'max_per_buyer': '3'}).data
    assert b'must not be above the normal price' in bad
    admin.post('/admin/deals', data={'product_id': '5', 'tiers': '1:2100, 5:1900', 'ends_at': '2099-01-01T10:00', 'max_per_buyer': '3', 'title': 'Kettle week'})
    row = db().execute('SELECT product_id, tiers, max_per_buyer, ends_at, status FROM group_deals').fetchone()
    assert row[0] == 5 and json.loads(row[1]) == [[1, 2100], [5, 1900]] and row[2] == 3 and row[4] == 'active'
    assert str(row[3]).startswith('2099-01-01 07:00')                         # 10:00 Nairobi = 07:00 UTC
    assert b'already has a running deal' in admin.post('/admin/deals', data={'product_id': '5', 'tiers': '1:2000', 'ends_at': '2099-01-01T10:00', 'max_per_buyer': '3'}).data
    assert b'Pamoja deal: buy together' in admin.get('/product/5').data
    home = admin.get('/').data
    assert b'Kettle week' in home and b'/deals/' in home
