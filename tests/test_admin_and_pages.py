"""Admin tools (photos, delivery, settings, categories) and public pages."""
import glob
import os
import re

from conftest import fake_png


def test_product_photo_gallery(app, login, db):
    adm = login('admin', 'adminpass')
    base = {'name': 'Gallery Phone', 'price': '1000', 'category': 'Electronics', 'stock': '5', 'description': 'Phone with photos'}
    adm.post('/admin/products/add', data={**base, 'photos': [(fake_png(), 'front.png'), (fake_png(), 'back.png'), (fake_png(), 'side.png')]},
             content_type='multipart/form-data')
    pid, main = db().execute("SELECT id, image FROM products WHERE name='Gallery Phone'").fetchone()
    extras = [r[0] for r in db().execute('SELECT path FROM product_images WHERE product_id=? ORDER BY sort_order', (pid,))]
    assert main.endswith('_front.png') and len(extras) == 2
    edit = adm.get(f'/admin/products/edit/{pid}').data
    assert edit.count(b'name="remove_photo"') == 3 and b'Tips for great photos' in edit

    side = next(p for p in extras if p.endswith('_side.png')); back = next(p for p in extras if p.endswith('_back.png'))
    adm.post(f'/admin/products/edit/{pid}', data={**base, 'main_photo': side, 'remove_photo': [back], 'photos': [(fake_png(), 'box.png')]},
             content_type='multipart/form-data')
    assert db().execute('SELECT image FROM products WHERE id=?', (pid,)).fetchone()[0] == side
    extras2 = [r[0] for r in db().execute('SELECT path FROM product_images WHERE product_id=?', (pid,))]
    assert back not in extras2 and not os.path.exists(os.path.join(app.static_folder, back)) and len(extras2) == 2

    page = app.test_client().get(f'/product/{pid}').data
    assert len(re.findall(rb'<button type="button" class="gallery-thumb', page)) == 3
    assert b'og:title" content="Gallery Phone - KSh 1,000"' in page and side.encode() in page
    assert b'name="description" content="KSh 1,000 - Phone with photos"' in page

    adm.post(f'/admin/products/edit/{pid}', data={**base, 'photos': [(fake_png(), f'p{i}.png') for i in range(10)]}, content_type='multipart/form-data')
    assert 1 + db().execute('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid,)).fetchone()[0] == 8   # max 8 photos

    files = [r[0] for r in db().execute('SELECT path FROM product_images WHERE product_id=?', (pid,))] + [side]
    adm.post(f'/admin/products/delete/{pid}')
    assert not any(os.path.exists(os.path.join(app.static_folder, p)) for p in files)


def test_cannot_delete_product_or_member_with_orders(login, db, checkout_details):
    jane = login('jane', 'janepass'); jane.post('/cart/add/1', data={'quantity': 1})
    jane.post('/checkout', data={**checkout_details, 'payment_method': 'Cash on Delivery'})
    adm = login('admin', 'adminpass')
    assert b'past orders' in adm.post('/admin/products/delete/1', follow_redirects=True).data
    assert b'has orders' in adm.post('/admin/members/delete/2', follow_redirects=True).data
    assert db().execute('SELECT COUNT(*) FROM products WHERE id=1').fetchone()[0] == 1


def test_old_categories_kept_when_editing(login, db):
    pid = db().execute("SELECT id FROM products WHERE category='Books'").fetchone()[0]
    page = login('admin', 'adminpass').get(f'/admin/products/edit/{pid}').data
    assert b'<option selected value="Books">' in page or b'value="Books" selected' in page


def test_delivery_options_and_shop_settings(app, login, db):
    adm = login('admin', 'adminpass')
    assert db().execute('SELECT COUNT(*) FROM delivery_zones').fetchone()[0] == 5               # defaults
    adm.post('/admin/delivery', data={'name': 'Mombasa', 'kind': 'delivery', 'fee': '700', 'eta': '3 days', 'sort_order': '5', 'is_active': 'y'})
    assert db().execute("SELECT fee FROM delivery_zones WHERE name='Mombasa'").fetchone()[0] == 700
    adm.post('/admin/settings', data={'phone': '0712 345 678', 'whatsapp': '0712 345 678', 'email': 'help@esashop.co.ke',
                                      'address': 'Moi Avenue, Nairobi', 'hours': 'Mon-Sat', 'returns_days': '7'})
    home = app.test_client().get('/').data
    assert b'help@esashop.co.ke' in home and b'wa.me/254712345678' in home
    assert b'Mombasa' in app.test_client().get('/delivery-info').data


def test_public_pages_and_files(app):
    g = app.test_client()
    for path in ['/', '/contact', '/delivery-info', '/returns', '/privacy', '/terms', '/login', '/register', '/reset_password', '/verify_email']:
        assert g.get(path).status_code == 200, path
    assert g.get('/no-such-page').status_code == 404
    assert g.get('/favicon.ico').status_code == 200 and b'Disallow: /admin/' in g.get('/robots.txt').data
    home = g.get('/').data
    assert b'og-default.png' in home and b'theme-color' in home


def test_every_template_compiles_and_every_link_exists(app):
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in glob.glob(os.path.join(root, 'templates', '**', '*.*'), recursive=True):
        name = os.path.relpath(path, os.path.join(root, 'templates'))
        app.jinja_env.get_template(name)
        for ep in re.findall(r"url_for\(\s*'([\w.]+)'", open(path).read()):
            assert ep in endpoints, f'{name}: unknown page {ep}'


def test_dashboard_today_at_a_glance(login, db):
    """Top of the admin dashboard: today's orders and sales (shop time zone; cash on delivery counts, cancelled doesn't)."""
    from datetime import datetime, timezone
    from shop.admin.dashboard import today_start_utc
    # 22:30 UTC is already 01:30 next day in Nairobi, so "today" started at 21:00 UTC
    assert today_start_utc(datetime(2026, 11, 27, 22, 30, tzinfo=timezone.utc)) == '2026-11-27 21:00:00'
    c = db()
    for status, amount in [('Pending', 1000), ('Processing', 2000), ('Cancelled', 500)]:
        c.execute("INSERT INTO orders(user_id,total_amount,status,payment_method) VALUES(2,?,?,'Cash on Delivery')", (amount, status))
    c.execute("INSERT INTO orders(user_id,order_date,total_amount,status) VALUES(2,'2020-01-01 10:00:00',9999,'Delivered')")
    c.commit()
    page = login('admin', 'adminpass').get('/admin/dashboard').data.decode()
    kpi = dict(re.findall(r'kpi-label">([^<]+)</span>.*?kpi-value">([^<]+)</span>', page, re.S))
    assert kpi == {'Orders today': '3', 'Sales today': 'KSh 3,000', 'Awaiting action': '2', 'Low stock': '2'}
