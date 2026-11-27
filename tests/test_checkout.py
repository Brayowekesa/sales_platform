"""Checkout, delivery fees, stock and M-Pesa."""
import pytest

import shop.views.orders as orders_view
from shop.extensions import mail
from shop.payments import mpesa_callback_signature


def zone_id(db, name):
    return str(db().execute('SELECT id FROM delivery_zones WHERE name=?', (name,)).fetchone()[0])


def test_order_with_delivery_fee_phone_and_confirmation_email(login, db):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 2})                     # 2 x 899 = 1798, under the free-delivery limit
    r = jane.get('/checkout')
    assert b'Nairobi CBD' in r.data and b'value="jane"' in r.data      # options shown, name prefilled
    with mail.record_messages() as outbox:
        r = jane.post('/checkout', data={'contact_name': 'Jane W', 'contact_phone': '0712 345 678',
                                         'delivery_zone': zone_id(db, 'Nairobi CBD'), 'shipping_address': 'Moi Ave, Bihi Towers',
                                         'payment_method': 'Cash on Delivery', 'total_amount': '1'})   # tampered total is ignored
    o = db().execute('SELECT id, subtotal, delivery_fee, total_amount, contact_phone, delivery_zone, status FROM orders').fetchone()
    assert o[1:] == (1798, 150, 1948, '0712345678', 'Nairobi CBD', 'Processing')
    assert r.headers['Location'].endswith(f'/orders/{o[0]}?placed=1')
    assert len(outbox) == 1 and 'KSh 1,948' in outbox[0].body
    assert db().execute("SELECT phone FROM users WHERE username='jane'").fetchone()[0] == '0712345678'
    page = jane.get(f'/orders/{o[0]}?placed=1').data
    assert b'Thank you, your order is placed' in page and b'order-tracker' in page


def test_checkout_requires_phone_and_address(login, db):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    base = {'contact_name': 'Jane', 'delivery_zone': zone_id(db, 'Nairobi CBD'), 'payment_method': 'Cash on Delivery'}
    assert b'We need a phone number' in jane.post('/checkout', data={**base, 'contact_phone': '', 'shipping_address': 'Moi Ave'}).data
    assert b'Please enter your delivery address' in jane.post('/checkout', data={**base, 'contact_phone': '0712345678', 'shipping_address': ''}).data
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_free_delivery_threshold_and_pickup(login, db):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/1', data={'quantity': 1})                     # 4999 >= 3000 -> free
    jane.post('/checkout', data={'contact_name': 'J', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                 'shipping_address': 'Moi Ave', 'payment_method': 'Cash on Delivery'})
    assert db().execute('SELECT delivery_fee, total_amount FROM orders ORDER BY id DESC').fetchone() == (0.0, 4999.0)
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'J', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Pick up from our shop'),
                                 'shipping_address': '', 'payment_method': 'Cash on Delivery'})
    oid, method, fee = db().execute('SELECT id, delivery_method, delivery_fee FROM orders ORDER BY id DESC').fetchone()
    assert (method, fee) == ('pickup', 0)
    assert b'Ready for pickup' in jane.get(f'/orders/{oid}').data


def test_two_buyers_cannot_both_get_the_last_item(app, login, db, checkout_details, monkeypatch):
    c = db()
    for u in ('race_a', 'race_b'):
        c.execute("INSERT INTO users(username,email,password_hash,confirmed) VALUES(?,?,?,1)",
                  (u, f'{u}@x.com', 'pbkdf2:sha256:1$x$' + '0' * 64))
    c.execute("INSERT INTO products(name,price,stock,category) VALUES('Last Item',100,1,'Electronics')"); c.commit()
    pid = c.execute("SELECT id FROM products WHERE name='Last Item'").fetchone()[0]
    b = login('jane', 'janepass'); b.post(f'/cart/add/{pid}', data={'quantity': 1})
    # Another buyer takes the item between B opening checkout and B's order being saved
    real = orders_view.get_db_connection
    calls = {'n': 0}

    def racing():
        calls['n'] += 1
        if calls['n'] == 2:   # 1st call reads the cart, 2nd opens the order transaction
            o = real(); o.execute('UPDATE products SET stock=0 WHERE id=?', (pid,)); o.commit(); o.close()
        return real()
    monkeypatch.setattr(orders_view, 'get_db_connection', racing)
    r = b.post('/checkout', data={**checkout_details, 'payment_method': 'Cash on Delivery'})
    assert b'just sold out' in r.data
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
    assert db().execute('SELECT stock FROM products WHERE id=?', (pid,)).fetchone()[0] == 0


def test_payment_method_and_phone_validation(login, checkout_details):
    jane = login('jane', 'janepass'); jane.post('/cart/add/3', data={'quantity': 1})
    assert b'Please choose Cash on Delivery' in jane.post('/checkout', data={**checkout_details, 'payment_method': 'Credit Card'}).data
    r = jane.post('/checkout', data={**checkout_details, 'payment_method': 'Lipa na M-Pesa', 'phone_number': '12345'})
    assert b'Enter a Safaricom number' in r.data


def test_mpesa_unreachable_keeps_cart_and_shows_one_error(login, db, checkout_details, monkeypatch):
    import shop.views.orders as ov
    monkeypatch.setattr(ov, 'lipa_na_mpesa_stk_push', lambda *a: {'ResponseCode': '1', 'ResponseDescription': 'M-Pesa is not reachable right now.'})
    jane = login('jane', 'janepass'); jane.post('/cart/add/3', data={'quantity': 1})
    r = jane.post('/checkout', data={**checkout_details, 'payment_method': 'Lipa na M-Pesa', 'phone_number': '+254712345678'})
    assert r.data.count(b'alert-danger') == 1 and b'not reachable' in r.data
    assert db().execute('SELECT COUNT(*) FROM carts').fetchone()[0] == 1
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_cancel_returns_stock_once_and_reopen_takes_it_again(login, db, checkout_details):
    jane = login('jane', 'janepass'); jane.post('/cart/add/5', data={'quantity': 1})
    jane.post('/checkout', data={**checkout_details, 'payment_method': 'Cash on Delivery'})
    oid = db().execute('SELECT id FROM orders').fetchone()[0]
    stock = lambda: db().execute('SELECT stock FROM products WHERE id=5').fetchone()[0]
    adm = login('admin', 'adminpass')
    s0 = stock()
    adm.post(f'/admin/orders/update_status/{oid}', data={'status': 'Cancelled'}); s1 = stock()
    adm.post(f'/admin/orders/update_status/{oid}', data={'status': 'Cancelled'}); s2 = stock()
    adm.post(f'/admin/orders/update_status/{oid}', data={'status': 'Processing'}); s3 = stock()
    assert (s1, s2, s3) == (s0 + 1, s0 + 1, s0)


def test_status_update_note_reaches_customer(login, db, checkout_details):
    jane = login('jane', 'janepass'); jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={**checkout_details, 'payment_method': 'Cash on Delivery'})
    oid = db().execute('SELECT id FROM orders').fetchone()[0]
    adm = login('admin', 'adminpass')
    with mail.record_messages() as outbox:
        adm.post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped', 'note': 'Rider: John, 0700 111 222'})
    assert len(outbox) == 1 and 'ready for pickup' in outbox[0].subject.lower() and 'Rider: John' in outbox[0].body
    assert b'Rider: John' in jane.get(f'/orders/{oid}').data
    admin_page = adm.get(f'/admin/orders/{oid}').data
    assert b'wa.me/254712345678' in admin_page and b'Rider: John' in admin_page


@pytest.fixture
def pending_mpesa_order(app, db):
    c = db()
    c.execute("INSERT INTO orders(user_id,total_amount,status,payment_method,transaction_id) VALUES(2,500,'Pending Payment','Lipa na M-Pesa','ws_CO_1')")
    oid = c.execute('SELECT max(id) FROM orders').fetchone()[0]
    c.execute('INSERT INTO order_items(order_id,product_id,quantity,price) VALUES(?,5,2,250)', (oid,)); c.commit()
    with app.app_context():
        sig = mpesa_callback_signature(str(oid))
    return oid, sig


def callback_body(code, amount):
    return {'Body': {'stkCallback': {'ResultCode': code, 'CheckoutRequestID': 'ws_CO_1', 'ResultDesc': 'x',
                                     'CallbackMetadata': {'Item': [{'Name': 'Amount', 'Value': amount}, {'Name': 'MpesaReceiptNumber', 'Value': 'R1'}]}}}}


def test_mpesa_callback_security_and_amount_check(app, db, pending_mpesa_order):
    oid, sig = pending_mpesa_order
    cl = app.test_client()
    status = lambda: db().execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()[0]
    assert cl.post(f'/mpesa_callback?order_id={oid}', json=callback_body(0, 500)).status_code == 403
    assert cl.post(f'/mpesa_callback?order_id={oid}&sig={"0" * 64}', json=callback_body(0, 500)).status_code == 403
    cl.post(f'/mpesa_callback?order_id={oid}&sig={sig}', json=callback_body(0, 1))
    assert status() == 'Pending Payment'                                     # underpaid
    cl.post(f'/mpesa_callback?order_id={oid}&sig={sig}', json=callback_body(0, 500))
    assert status() == 'Paid'
    cl.post(f'/mpesa_callback?order_id={oid}&sig={sig}', json=callback_body(1032, 0))
    assert status() == 'Paid'                                                # a late failure can't undo it


def test_failed_mpesa_payment_returns_stock(app, db, pending_mpesa_order):
    oid, sig = pending_mpesa_order
    before = db().execute('SELECT stock FROM products WHERE id=5').fetchone()[0]
    app.test_client().post(f'/mpesa_callback?order_id={oid}&sig={sig}', json=callback_body(1032, 0))
    assert db().execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()[0] == 'Payment Failed'
    assert db().execute('SELECT stock FROM products WHERE id=5').fetchone()[0] == before + 2
