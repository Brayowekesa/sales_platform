"""Fixes from the security review: stock, PINs, rider links, gift SMS, honest prices, proof answers."""
import io

import pytest
from werkzeug.datastructures import FileStorage

import shop.notifications as notifications
from shop.delivery import order_from_rider_token, rider_token, save_proof_file
from shop.extensions import mail


def zone_id(db, name):
    return str(db().execute('SELECT id FROM delivery_zones WHERE name=?', (name,)).fetchone()[0])


@pytest.fixture
def order(login, db):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                 'shipping_address': 'Moi Ave', 'payment_method': 'Cash on Delivery'})
    return db().execute('SELECT MAX(id) FROM orders').fetchone()[0]


def stock(db):
    return db().execute('SELECT stock FROM products WHERE id = 3').fetchone()[0]


def test_stock_goes_back_only_once(login, db, order):
    admin = login('admin', 'adminpass')
    before = stock(db)
    for _ in range(3):                                                   # double/triple click on Cancel
        admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Cancelled'})
    assert stock(db) == before + 1
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Refunded'})
    assert stock(db) == before + 1                                       # refunding a cancelled order keeps stock
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Processing'})
    assert stock(db) == before                                           # reopened: taken out again, once
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Paid'})
    assert stock(db) == before


def test_pin_locks_after_ten_wrong_tries_and_hides_staff_details(app, login, db, order):
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})
    pin = db().execute('SELECT delivery_pin FROM orders WHERE id=?', (order,)).fetchone()[0]
    wrong = '0000' if pin != '0000' else '1111'
    with app.test_request_context():
        link = f'/deliver/{rider_token(order)}'
    rider = app.test_client()
    for i in range(10):
        c = db(); c.execute('DELETE FROM rate_limit_events'); c.commit()   # past the 15-minute limit
        rider.post(link, data={'pin': wrong})
    c = db(); c.execute('DELETE FROM rate_limit_events'); c.commit()
    assert b'locked after too many wrong tries' in rider.post(link, data={'pin': pin}).data
    assert db().execute('SELECT status FROM orders WHERE id=?', (order,)).fetchone()[0] == 'Shipped'
    assert b'PIN is locked' in admin.get(f'/admin/orders/{order}').data


def test_pin_confirmation_note_has_no_staff_name(login, db, order):
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})
    pin = db().execute('SELECT delivery_pin FROM orders WHERE id=?', (order,)).fetchone()[0]
    admin.post(f'/admin/orders/{order}/confirm-delivery', data={'pin': pin})
    note = db().execute("SELECT note FROM order_status_history WHERE order_id=? AND status='Delivered'", (order,)).fetchone()[0]
    assert note == "Confirmed with the customer's PIN by our staff" and 'admin' not in note


def test_rider_link_stops_working_after_a_new_pin(app, login, db, order):
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})
    with app.test_request_context():
        old = rider_token(order)
        assert order_from_rider_token(old) == order
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Processing'})
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})   # sent again: new PIN
    with app.test_request_context():
        assert order_from_rider_token(old) is None
        assert order_from_rider_token(rider_token(order)) == order
    assert app.test_client().get(f'/deliver/{old}').status_code == 410


def test_gift_message_rules_and_daily_limit(login, db, monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, 'send_sms', lambda p, t: sent.append((p, t)) or True)
    jane = login('jane', 'janepass')
    base = {'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
            'shipping_address': 'Kakamega', 'payment_method': 'Cash on Delivery', 'is_gift': 'y',
            'recipient_name': 'Mama', 'recipient_phone': '0722000111'}
    for bad in ('M-PESA reversal: confirm at http://evil.example/x', 'Visit evil.example/x', 'call 0700 111 222'):
        jane.post('/cart/add/3', data={'quantity': 1})
        assert b'no links or phone numbers' in jane.post('/checkout', data={**base, 'gift_message': bad}).data
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0 and not sent
    for _ in range(3):
        jane.post('/cart/add/3', data={'quantity': 1})
        jane.post('/checkout', data={**base, 'gift_message': 'Happy birthday Mum!'})
    jane.post('/cart/add/3', data={'quantity': 1})
    assert b'up to 3 gift orders a day' in jane.post('/checkout', data={**base, 'gift_message': 'Hi'}).data
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 3


def test_gift_sms_uses_a_clean_first_name(login, db, monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, 'send_sms', lambda p, t: sent.append((p, t)) or True)
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'http://evil.example/x Jane', 'contact_phone': '0712345678',
                                 'delivery_zone': zone_id(db, 'Nairobi CBD'), 'shipping_address': 'Kakamega',
                                 'payment_method': 'Cash on Delivery', 'is_gift': 'y', 'recipient_name': 'Mama', 'recipient_phone': '0722000111'})
    to_mum = [t for p, t in sent if p == '0722000111'][0]
    assert to_mum.split('Track:')[0].startswith('ESA E-Shop: Jane has sent you a parcel') or 'Jane has sent you a parcel' in to_mum
    assert 'http' not in to_mum.split('Track:')[0] and 'evil' not in to_mum


def edit_product(admin, price, original='', stock=10):
    return admin.post('/admin/products/edit/5', data={'name': 'Kettle', 'price': str(price), 'original_price': str(original),
                                                      'category': 'Home & Kitchen', 'stock': str(stock), 'description': 'Steel kettle'},
                      content_type='multipart/form-data')


def backdate(db, days):
    c = db()
    if c.raw.__class__.__module__.startswith('sqlite3'):
        c.execute(f"UPDATE price_history SET changed_at = datetime(changed_at, '-{days} days') WHERE product_id = 5")
    else:
        c.execute(f"UPDATE price_history SET changed_at = changed_at - interval '{days} days' WHERE product_id = 5")
    c.commit()


def test_sale_limit_cannot_be_reset_by_changing_the_was_price(app, login, db):
    admin = login('admin', 'adminpass')
    backdate(db, 20)                                                     # sold at 2,199 for 20 days
    edit_product(admin, 1799, 2199)                                      # real sale starts
    backdate(db, 61)                                                     # ...61 days ago
    edit_product(admin, 1799, 2199)                                      # saving again: start date comes from history
    assert b'Checked discount' not in app.test_client().get('/product/5').data
    edit_product(admin, 1799, 2198)                                      # nudge the was-price
    edit_product(admin, 1799, '')
    edit_product(admin, 1799, 2199)                                      # clear and set again
    assert b'Checked discount' not in app.test_client().get('/product/5').data


def test_price_bounce_does_not_send_a_drop_alert(login, db):
    jane = login('jane', 'janepass')
    jane.post('/wishlist/add/5')
    admin = login('admin', 'adminpass')
    edit_product(admin, 9999)                                            # up for a moment...
    with mail.record_messages() as outbox:
        edit_product(admin, 2199)                                        # ...and back: not a real drop
    assert len(outbox) == 0
    with mail.record_messages() as outbox:
        edit_product(admin, 1999)                                        # genuinely the lowest in 30 days
    assert len(outbox) == 1 and 'KSh 1,999' in outbox[0].body and 'KSh 2,199' in outbox[0].body


def test_proof_file_keeps_its_type(app):
    with app.test_request_context():
        name = save_proof_file(FileStorage(io.BytesIO(b'x'), filename='фото.JPG'))
        assert name.endswith('.jpg') and '/' not in name
        name = save_proof_file(FileStorage(io.BytesIO(b'x'), filename='.mp4'))
        assert name.endswith('.mp4')


def test_proof_answer_only_while_checking(login, db, order):
    jane = login('jane', 'janepass')
    jane.post(f'/orders/{order}/proof-response', data={'answer': 'ok'})  # no photos yet
    assert db().execute('SELECT proof_response FROM orders WHERE id=?', (order,)).fetchone()[0] is None
