"""Built for Kenyan phones: small photos, data saver, WhatsApp, landmarks, installable app."""
import io
import os
import uuid

import pytest
from PIL import Image

from shop.delivery import rider_token


def zone_id(db, name):
    return str(db().execute('SELECT id FROM delivery_zones WHERE name=?', (name,)).fetchone()[0])


@pytest.fixture
def photo(app, tmp_path):
    """A big phone photo with GPS-style EXIF data, saved as a product photo."""
    name = f'uploads/_test_{uuid.uuid4().hex}.jpg'
    full = os.path.join(app.static_folder, name)
    exif = Image.Exif(); exif[0x010F] = 'PhoneMaker'           # camera make tag
    Image.new('RGB', (3000, 2000), (200, 80, 40)).save(full, 'JPEG', exif=exif)
    app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')   # resized copies go here during tests
    with app.app_context():
        from shop.db import get_db_connection
        with get_db_connection() as conn:
            conn.execute('UPDATE products SET image = ? WHERE id = 1', (name,))
    yield name
    os.remove(full)


def test_resized_webp_photos_and_data_saver(app, photo):
    client = app.test_client()
    page = client.get('/?category=Electronics').data.decode()
    assert '/img/card/' + photo in page
    url = page.split('/img/card/' + photo)[0].rsplit('"', 1)[1] + '/img/card/' + photo + page.split('/img/card/' + photo)[1].split('"')[0]
    r = client.get(url.replace('&amp;', '&'))
    assert r.status_code == 200 and r.mimetype == 'image/webp' and 'immutable' in r.headers['Cache-Control']
    im = Image.open(io.BytesIO(r.data))
    assert max(im.size) == 480 and not im.getexif()               # small, and camera data removed
    big = os.path.getsize(os.path.join(app.static_folder, photo))
    assert len(r.data) < big / 5

    # data saver: even smaller photos, no web fonts
    r = client.get('/data-saver/on?next=/')
    assert r.status_code == 302 and r.headers['Location'].endswith('/')
    page = client.get('/?category=Electronics').data.decode()
    assert '/img/card_lite/' in page and 'fonts.googleapis.com' not in page and 'Data saver: ON' in page
    client.get('/data-saver/off')
    assert 'fonts.googleapis.com' in client.get('/').data.decode()


def test_image_route_refuses_bad_requests(app, photo):
    client = app.test_client()
    assert client.get('/img/huge/' + photo).status_code == 404
    assert client.get('/img/card/../shop/config.py').status_code == 404
    assert client.get('/img/card/uploads/missing.jpg').status_code == 404
    assert client.get('/data-saver/on?next=https://evil.example').headers['Location'] in ('/', 'http://localhost/')


def test_whatsapp_button_with_product_message(app, login, db):
    page = app.test_client().get('/product/5').data.decode()
    assert 'wa-float' not in page                                   # no number set: hidden
    c = db(); c.execute("INSERT INTO settings (key, value) VALUES ('whatsapp', '0712 345 678')"); c.commit()
    page = app.test_client().get('/product/5').data.decode()
    assert 'https://wa.me/254712345678?text=' in page and 'Electric%20Kettle' in page and 'Ask on WhatsApp' in page


def test_landmark_and_map_pin_reach_the_rider(app, login, db):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                 'shipping_address': 'Moi Ave', 'landmark': 'Green gate opposite Total petrol station',
                                 'location': '-1.283253,36.823543', 'payment_method': 'Cash on Delivery'})
    oid, landmark, lat, lng = db().execute('SELECT id, delivery_landmark, delivery_lat, delivery_lng FROM orders').fetchone()
    assert landmark == 'Green gate opposite Total petrol station' and round(lat, 4) == -1.2833 and round(lng, 4) == 36.8235
    admin = login('admin', 'adminpass')
    assert b'Open pin in Google Maps' in admin.get(f'/admin/orders/{oid}').data
    admin.post(f'/admin/orders/update_status/{oid}', data={'status': 'Shipped'})
    with app.test_request_context():
        page = app.test_client().get(f'/deliver/{rider_token(oid)}').data.decode()
    assert "Green gate opposite Total" in page and 'google.com/maps/search/?api=1&query=-1.283253,36.823543' in page


def test_bad_location_is_ignored_and_pickup_has_none(login, db):
    jane = login('jane', 'janepass')
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                 'shipping_address': 'Moi Ave', 'location': '999,abc', 'payment_method': 'Cash on Delivery'})
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Pick up from our shop'),
                                 'landmark': 'x', 'location': '-1.2,36.8', 'payment_method': 'Cash on Delivery'})
    rows = db().execute('SELECT delivery_landmark, delivery_lat, delivery_lng FROM orders ORDER BY id').fetchall()
    assert rows == [(None, None, None), (None, None, None)]


def test_installable_app_files(app):
    client = app.test_client()
    m = client.get('/manifest.webmanifest')
    assert m.mimetype == 'application/manifest+json' and m.json['display'] == 'standalone'
    assert {i['sizes'] for i in m.json['icons']} == {'192x192', '512x512'}
    for icon in m.json['icons']:
        assert client.get(icon['src']).status_code == 200
    sw = client.get('/sw.js')
    assert sw.mimetype == 'application/javascript' and b'OFFLINE_URL' in sw.data and 'no-cache' in sw.headers['Cache-Control']
    assert b"You're offline" in client.get('/offline').data or b'You&#39;re offline' in client.get('/offline').data
    home = client.get('/')
    assert b'rel="manifest"' in home.data and b'install-app' in home.data
    assert "worker-src 'self'" in home.headers['Content-Security-Policy']
    assert 'geolocation=(self)' in home.headers['Permissions-Policy']
