"""Access control, headers, uploads and other protections."""
import sqlite3

import shop.views.cart as cart_view
from conftest import fake_png


def test_guests_can_browse_but_not_buy(app):
    g = app.test_client()
    r = g.get('/'); assert r.status_code == 200 and b'product-card' in r.data
    assert g.get('/search?query=note').status_code == 200
    assert g.get('/product/1').status_code == 200
    assert g.get('/cart').headers['Location'].endswith('/login?next=/cart')
    r = g.post('/wishlist/add/1', headers={'Referer': 'http://localhost/product/1'})
    assert r.headers['Location'].endswith('/login?next=/product/1')           # back to the product, not the POST url
    r = g.post('/cart/add/1', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 401 and r.get_json()['login_required']
    assert '/login' in g.get('/checkout').headers['Location']


def test_admin_section_closed_to_guests_and_customers(app, login):
    g = app.test_client()
    jane = login('jane', 'janepass')
    for path in ['/admin/dashboard', '/admin/products', '/admin/settings', '/admin/delivery', '/admin/reports/low-stock']:
        assert '/login' in g.get(path).headers['Location']
        assert jane.get(path).status_code == 302 and '/admin' not in jane.get(path).headers['Location']


def test_customers_cannot_see_other_peoples_orders(login, db, checkout_details):
    jane = login('jane', 'janepass'); jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={**checkout_details, 'payment_method': 'Cash on Delivery'})
    oid = db().execute('SELECT id FROM orders').fetchone()[0]
    c = db(); c.execute("INSERT INTO users(username,email,password_hash,confirmed) SELECT 'eve','eve@x.com',password_hash,1 FROM users WHERE username='jane'"); c.commit()
    assert login('eve', 'janepass').get(f'/orders/{oid}').status_code == 302


def test_security_headers(app):
    h = app.test_client().get('/').headers
    for k in ['Content-Security-Policy', 'X-Frame-Options', 'X-Content-Type-Options', 'Referrer-Policy']:
        assert k in h
    assert "frame-ancestors 'none'" in h['Content-Security-Policy'] and "object-src 'none'" in h['Content-Security-Policy']


def test_open_redirect_blocked(app):
    r = app.test_client().post('/login?next=https://evil.example/', data={'login_identifier': 'jane', 'password': 'janepass'})
    assert r.headers['Location'] == '/'


def test_uploads_must_really_be_images(login, db):
    adm = login('admin', 'adminpass')
    base = {'price': '100', 'category': 'Electronics', 'stock': '1'}
    adm.post('/admin/products/add', data={**base, 'name': 'Good', 'photos': [(fake_png(), 'good.png')]}, content_type='multipart/form-data')
    adm.post('/admin/products/add', data={**base, 'name': 'Fake', 'photos': [(__import__('io').BytesIO(b'<script>alert(1)</script>'), 'evil.png')]},
             content_type='multipart/form-data')
    good = db().execute("SELECT image FROM products WHERE name='Good'").fetchone()[0]
    fake = db().execute("SELECT image FROM products WHERE name='Fake'").fetchone()[0]
    assert good.endswith('_good.png') and fake == 'uploads/default.jpg'
    good_id = db().execute("SELECT id FROM products WHERE name='Good'").fetchone()[0]
    adm.post(f'/admin/products/delete/{good_id}')   # also removes the uploaded test file


def test_promotion_and_settings_links_validated(login, db):
    adm = login('admin', 'adminpass')
    assert b'Use a link that starts with' in adm.post('/admin/promotions/add', data={'title': 'x', 'target_url': 'javascript:alert(1)', 'display_order': '0'}).data
    adm.post('/admin/settings', data={'instagram': 'javascript:alert(1)'})
    assert db().execute('SELECT COUNT(*) FROM settings').fetchone()[0] == 0


def test_database_errors_not_shown_to_customers(login, monkeypatch):
    jane = login('jane', 'janepass')
    def broken():
        raise sqlite3.OperationalError('disk I/O error at /secret/path/sales.db')
    monkeypatch.setattr(cart_view, 'get_db_connection', broken)
    jane.post('/cart/empty')
    monkeypatch.undo()
    assert b'/secret/path' not in jane.get('/cart').data


def test_admin_cannot_demote_themselves(login, db):
    adm = login('admin', 'adminpass')
    r = adm.post('/admin/members/edit/1', data={'username': 'admin', 'email': 'admin@example.com', 'password': '', 'confirm_password': '', 'confirmed': 'y'},
                 follow_redirects=True)
    assert db().execute('SELECT is_admin FROM users WHERE id=1').fetchone()[0] == 1 and b'remove your own admin rights' in r.data
