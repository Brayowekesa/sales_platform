"""Packing proof (photos/video before dispatch) and the delivery PIN."""
import io
import os
import re

import pytest

from conftest import fake_png
from shop.delivery import order_from_rider_token, rider_token
from shop.extensions import mail


def fake_mp4():
    return io.BytesIO(b'\x00\x00\x00\x18ftypmp42' + os.urandom(64))


@pytest.fixture
def order(login, db, checkout_details):
    """A delivery order placed by jane; returns its id."""
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    zone = db().execute("SELECT id FROM delivery_zones WHERE name='Nairobi CBD'").fetchone()[0]
    jane.post('/checkout', data={**checkout_details, 'delivery_zone': str(zone), 'shipping_address': 'Moi Ave', 'payment_method': 'Cash on Delivery'})
    return db().execute('SELECT MAX(id) FROM orders').fetchone()[0]


def test_packing_proof_upload_privacy_and_customer_answer(app, login, db, order):
    admin = login('admin', 'adminpass')
    with mail.record_messages() as outbox:
        admin.post(f'/admin/orders/{order}/proof', data={'proof': [(fake_png(), 'item.png'), (fake_mp4(), 'packing.mp4'),
                                                                     (io.BytesIO(b'<html>'), 'evil.mp4')]},
                   content_type='multipart/form-data')
    rows = db().execute('SELECT id, kind, path FROM order_proofs WHERE order_id=? ORDER BY id', (order,)).fetchall()
    assert [r[1] for r in rows] == ['photo', 'video']                         # the fake video was refused
    assert not any(os.path.exists(os.path.join(app.static_folder, 'uploads', r[2])) for r in rows)   # not in public static/
    assert len(outbox) == 1 and 'Check your item' in outbox[0].subject

    jane = login('jane', 'janepass')
    page = jane.get(f'/orders/{order}').data
    assert b'Your item, before we send it' in page and b'Looks right' in page
    assert jane.get(f'/orders/{order}/proof/{rows[0][0]}').status_code == 200
    assert admin.get(f'/orders/{order}/proof/{rows[0][0]}').status_code == 200
    assert app.test_client().get(f'/orders/{order}/proof/{rows[0][0]}').status_code == 302     # guest -> login
    c = db(); c.execute("INSERT INTO users(username,email,password_hash,confirmed) SELECT 'eve','eve@x.com',password_hash,1 FROM users WHERE username='jane'"); c.commit()
    assert login('eve', 'janepass').get(f'/orders/{order}/proof/{rows[0][0]}').status_code == 404  # other customers can't see it

    assert b'Please tell us what' in jane.post(f'/orders/{order}/proof-response', data={'answer': 'problem', 'note': ''}, follow_redirects=True).data
    jane.post(f'/orders/{order}/proof-response', data={'answer': 'problem', 'note': 'Wrong colour, I ordered black'})
    assert db().execute('SELECT proof_response, proof_response_note FROM orders WHERE id=?', (order,)).fetchone() == ('problem', 'Wrong colour, I ordered black')
    assert b'Wrong colour, I ordered black' in admin.get(f'/admin/orders/{order}').data


def test_delivery_pin_and_rider_link(app, login, db, order):
    admin = login('admin', 'adminpass')
    with mail.record_messages() as outbox:
        admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})
    pin = db().execute('SELECT delivery_pin FROM orders WHERE id=?', (order,)).fetchone()[0]
    assert re.fullmatch(r'\d{4}', pin)
    assert pin in outbox[0].body and 'only when you have your parcel' in outbox[0].body
    assert pin.encode() in login('jane', 'janepass').get(f'/orders/{order}').data            # shown to the customer

    admin_page = admin.get(f'/admin/orders/{order}').data.decode()
    link = re.search(r'id="rider-link" value="([^"]+)"', admin_page).group(1)
    path = link.replace('http://localhost', '')
    rider = app.test_client()
    page = rider.get(path).data
    assert b'Moi Ave' in page and b'0712345678' in page and b'Confirm delivered' in page

    wrong = '0000' if pin != '0000' else '1111'
    assert b'PIN is not right' in rider.post(path, data={'pin': wrong}).data
    assert db().execute('SELECT status FROM orders WHERE id=?', (order,)).fetchone()[0] == 'Shipped'
    with mail.record_messages() as outbox:
        r = rider.post(path, data={'pin': pin})
    assert b'confirmed with the customer' in r.data
    assert db().execute('SELECT status, delivered_with_pin FROM orders WHERE id=?', (order,)).fetchone() == ('Delivered', 1)
    assert 'delivered' in outbox[0].subject.lower()
    assert b'already marked delivered' in rider.post(path, data={'pin': pin}).data or b'Delivered' in rider.get(path).data


def test_wrong_pins_lock_out(app, login, db, order):
    login('admin', 'adminpass').post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})
    pin = db().execute('SELECT delivery_pin FROM orders WHERE id=?', (order,)).fetchone()[0]
    with app.test_request_context():
        path = f'/deliver/{rider_token(order)}'
    rider = app.test_client()
    wrong = '0000' if pin != '0000' else '1111'
    for _ in range(5):
        rider.post(path, data={'pin': wrong})
    assert b'Too many wrong PINs' in rider.post(path, data={'pin': pin}).data
    assert db().execute('SELECT status FROM orders WHERE id=?', (order,)).fetchone()[0] == 'Shipped'


def test_bad_or_expired_rider_links(app, order):
    assert app.test_client().get('/deliver/not-a-real-token').status_code == 410
    with app.test_request_context():
        token = rider_token(order)
        app.config['RIDER_LINK_DAYS'] = 0
        assert order_from_rider_token(token) is None or order_from_rider_token(token) == order   # 0 days: expires within the second
        app.config['RIDER_LINK_DAYS'] = 7
        assert order_from_rider_token(token) == order


def test_staff_confirm_and_override_rules(login, db, order):
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Shipped'})
    r = admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Delivered'}, follow_redirects=True)
    assert b'give a reason' in r.data
    assert db().execute('SELECT status FROM orders WHERE id=?', (order,)).fetchone()[0] == 'Shipped'
    admin.post(f'/admin/orders/update_status/{order}', data={'status': 'Delivered', 'override_reason': 'Customer phone off, neighbour received'})
    status, with_pin = db().execute('SELECT status, delivered_with_pin FROM orders WHERE id=?', (order,)).fetchone()
    assert (status, with_pin) == ('Delivered', 0)
    note = db().execute("SELECT note FROM order_status_history WHERE order_id=? AND status='Delivered'", (order,)).fetchone()[0]
    assert note == 'Marked delivered by our staff'                       # customers don't see staff names or reasons
    reason = db().execute('SELECT delivery_override_reason FROM orders WHERE id=?', (order,)).fetchone()[0]
    assert reason == 'admin: Customer phone off, neighbour received'
    assert b'neighbour received' in admin.get(f'/admin/orders/{order}').data
    assert b'neighbour received' not in login('jane', 'janepass').get(f'/orders/{order}').data


def test_staff_can_confirm_with_pin_at_pickup(login, db, checkout_details):
    jane = login('jane', 'janepass'); jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={**checkout_details, 'payment_method': 'Cash on Delivery'})          # pickup order
    oid = db().execute('SELECT MAX(id) FROM orders').fetchone()[0]
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped'})
    pin = db().execute('SELECT delivery_pin FROM orders WHERE id=?', (oid,)).fetchone()[0]
    assert b'Pickup PIN' in jane.get(f'/orders/{oid}').data
    assert b'id="rider-link"' not in admin.get(f'/admin/orders/{oid}').data                              # no rider for pickups
    admin.post(f'/admin/orders/{oid}/confirm-delivery', data={'pin': pin})
    assert db().execute('SELECT status, delivered_with_pin FROM orders WHERE id=?', (oid,)).fetchone() == ('Delivered', 1)
