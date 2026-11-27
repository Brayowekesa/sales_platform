"""Tuma kwa Mama: orders sent to someone else."""
import pytest

import shop.notifications as notifications
import shop.views.orders as orders_view
from shop.gifts import gift_token, order_from_gift_token


def zone_id(db, name):
    return str(db().execute('SELECT id FROM delivery_zones WHERE name=?', (name,)).fetchone()[0])


@pytest.fixture
def sms(monkeypatch):
    """Capture text messages instead of sending them."""
    sent = []
    fake = lambda phone, text: sent.append((phone, text)) or True
    monkeypatch.setattr(notifications, 'send_sms', fake)
    monkeypatch.setattr(orders_view, 'send_sms', fake)
    monkeypatch.setattr(orders_view, 'sms_enabled', lambda: True)
    return sent


def gift_order(jane, db, zone='Nairobi CBD', payment='Cash on Delivery', **extra):
    jane.post('/cart/add/3', data={'quantity': 2})
    data = {'contact_name': 'Wanjiku Kamau', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, zone),
            'shipping_address': 'Kakamega town, near the market', 'payment_method': payment,
            'is_gift': 'y', 'recipient_name': 'Mama Wanjiku', 'recipient_phone': '0722000111',
            'gift_message': 'Happy birthday Mum!', **extra}
    return jane.post('/checkout', data=data)


def test_gift_checkout_saves_sender_and_recipient(login, db, sms):
    jane = login('jane', 'janepass')
    r = gift_order(jane, db)
    assert r.status_code == 302
    row = db().execute('''SELECT id, is_gift, contact_name, contact_phone, sender_name, sender_phone, gift_message, gift_notified_at
                          FROM orders''').fetchone()
    oid = row[0]
    assert row[1:7] == (1, 'Mama Wanjiku', '0722000111', 'Wanjiku Kamau', '0712345678', 'Happy birthday Mum!')
    assert row[7] is not None                                           # recipient was told
    assert db().execute("SELECT phone FROM users WHERE username='jane'").fetchone()[0] == '0712345678'   # buyer's own phone kept
    to_buyer = [t for p, t in sms if p == '0712345678']
    to_mum = [t for p, t in sms if p == '0722000111']
    assert to_buyer and 'Mama' in to_buyer[0]
    assert len(to_mum) == 1 and 'Wanjiku has sent you a parcel' in to_mum[0] and 'Happy birthday Mum!' in to_mum[0]
    assert 'KSh 1,948' in to_mum[0]                                     # cash on delivery: amount to have ready
    assert '/gift/' in to_mum[0] and '899' not in to_mum[0]             # tracking link, no item prices
    page = jane.get(f'/orders/{oid}?placed=1').data.decode()
    assert 'Sending to' in page and 'Mama Wanjiku' in page and 'wa.me/254722000111' in page


def test_gift_needs_recipient_details(login, db, sms):
    jane = login('jane', 'janepass')
    assert b"Please enter the name of the person" in gift_order(jane, db, recipient_name='').data
    assert b'We need their phone number' in gift_order(jane, db, recipient_phone='').data
    assert b"your own number" in gift_order(jane, db, recipient_phone='+254712345678').data
    assert db().execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0


def test_pin_goes_to_recipient_and_tracking_page_has_no_prices(app, login, db, sms):
    jane = login('jane', 'janepass')
    gift_order(jane, db, payment='Cash on Delivery')
    oid = db().execute('SELECT id FROM orders').fetchone()[0]
    sms.clear()
    login('admin', 'adminpass').post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped'})
    pin = db().execute('SELECT delivery_pin FROM orders WHERE id=?', (oid,)).fetchone()[0]
    to_mum = [t for p, t in sms if p == '0722000111']
    to_buyer = [t for p, t in sms if p == '0712345678']
    assert len(to_mum) == 1 and pin in to_mum[0] and 'Wanjiku' in to_mum[0]
    assert to_buyer and pin not in to_buyer[0] and 'sent Mama the delivery PIN' in to_buyer[0]
    assert pin.encode() in jane.get(f'/orders/{oid}').data              # buyer can still see it

    with app.test_request_context():
        token = gift_token(oid)
    page = app.test_client().get(f'/gift/{token}').data.decode()
    assert 'Wanjiku sent you something' in page and 'Happy birthday Mum!' in page and 'Cotton T-Shirt' in page
    assert pin not in page and 'KSh 899' not in page and 'KSh 1,948' in page   # only the amount to pay
    assert app.test_client().get('/gift/not-a-token').status_code == 410
    with app.test_request_context():
        assert order_from_gift_token(token + 'x') is None


def test_rider_and_admin_see_gift(app, login, db, sms):
    jane = login('jane', 'janepass')
    gift_order(jane, db)
    oid = db().execute('SELECT id FROM orders').fetchone()[0]
    admin = login('admin', 'adminpass')
    page = admin.get(f'/admin/orders/{oid}').data.decode()
    assert 'Gift order' in page and 'Wanjiku Kamau' in page and 'Happy birthday Mum!' in page
    admin.post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped'})
    from shop.delivery import rider_token
    with app.test_request_context():
        page = app.test_client().get(f'/deliver/{rider_token(oid)}').data.decode()
    assert 'Gift from Wanjiku Kamau' in page and 'Mama Wanjiku' in page and '0722000111' in page


def test_resend_is_owner_only_and_limited(login, db, sms):
    jane = login('jane', 'janepass')
    gift_order(jane, db)
    oid = db().execute('SELECT id FROM orders').fetchone()[0]
    sms.clear()
    for _ in range(4):
        jane.post(f'/orders/{oid}/gift-resend')
    assert len([1 for p, _ in sms if p == '0722000111']) == 3          # 3 per hour
    c = db(); c.execute("INSERT INTO users(username,email,password_hash,confirmed) VALUES('bob','bob@example.com','x',1)"); c.commit()
    assert login('admin', 'adminpass').post(f'/orders/{oid}/gift-resend').status_code == 404   # not their order


def test_mpesa_gift_waits_for_payment_before_texting_recipient(login, db, sms, monkeypatch):
    monkeypatch.setattr(orders_view, 'lipa_na_mpesa_stk_push', lambda *a: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_1'})
    jane = login('jane', 'janepass')
    gift_order(jane, db, payment='Lipa na M-Pesa')
    assert not [1 for p, _ in sms if p == '0722000111']                 # not paid yet: nothing to Mum
    assert db().execute('SELECT gift_notified_at FROM orders').fetchone()[0] is None


def test_normal_order_is_unchanged(login, db, sms):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                 'shipping_address': 'Moi Ave', 'payment_method': 'Cash on Delivery',
                                 'recipient_name': 'ignored', 'recipient_phone': '0722000111'})
    assert db().execute('SELECT is_gift, contact_name, sender_name FROM orders').fetchone() == (0, 'Jane', None)
    assert [p for p, _ in sms] == ['0712345678']
