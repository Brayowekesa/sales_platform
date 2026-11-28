"""Photo resizer, upload limits, WebP product photos, settings, promo banners and copy-to-postgres."""
import io
import os
import subprocess
import sys
import uuid

import pytest
from PIL import Image

from conftest import PG_URL, fake_png

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def photo(app, tmp_path):
    """A product photo in static/uploads; resized copies go to a temporary folder."""
    name = f'uploads/_test_{uuid.uuid4().hex}.png'
    full = os.path.join(app.static_folder, name)
    Image.new('RGB', (600, 400), (20, 120, 200)).save(full, 'PNG')
    app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    yield name
    os.remove(full)


def cached_files(app):
    found = []
    for folder, _, files in os.walk(os.path.join(app.config['UPLOAD_FOLDER'], '_cache')):
        found += [os.path.join(folder, f) for f in files]
    return found


def test_each_photo_has_one_cached_copy_per_size(app, photo):
    client = app.test_client()
    assert client.get(f'/img/thumb/{photo}').status_code == 200
    assert len(cached_files(app)) == 1
    file_name = photo.split('/', 1)[1]
    for variant in [f'uploads//{file_name}', f'uploads/./{file_name}', f'uploads/promotions/../{file_name}',
                    f'uploads/./././{file_name}']:
        assert client.get(f'/img/thumb/{variant}').status_code != 200, variant
    assert len(cached_files(app)) == 1            # no extra copies (and no extra resizing)
    assert client.get(f'/img/thumb/{photo}').status_code == 200
    assert len(cached_files(app)) == 1


def test_resizer_refuses_cached_copies_and_non_photos(app, photo):
    client = app.test_client()
    static_uploads = os.path.join(app.static_folder, 'uploads')
    cache_dir = os.path.join(static_uploads, '_cache', 'thumb')
    made_cache_dir = not os.path.isdir(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    tag = uuid.uuid4().hex
    cached = os.path.join(cache_dir, f'_test_{tag}.png')
    not_photo = os.path.join(static_uploads, f'_test_{tag}.txt')
    try:
        Image.new('RGB', (300, 300)).save(cached, 'PNG')
        Image.new('RGB', (300, 300)).save(not_photo, 'PNG')       # real PNG data, wrong extension
        assert client.get(f'/img/card/uploads/_cache/thumb/_test_{tag}.png').status_code == 404
        assert client.get(f'/img/card/uploads/_test_{tag}.txt').status_code == 404
        assert cached_files(app) == []
    finally:
        os.remove(cached)
        os.remove(not_photo)
        if made_cache_dir:
            for d in (cache_dir, os.path.dirname(cache_dir)):
                try:
                    os.rmdir(d)
                except OSError:
                    pass


def test_decompression_bomb_warning_is_refused(app, photo, monkeypatch):
    # 600 x 400 = 240,000 px: between MAX_IMAGE_PIXELS and twice that, where Pillow only warns
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 200_000)
    assert app.test_client().get(f'/img/card/{photo}').status_code == 404
    assert cached_files(app) == []


def test_upload_limit_shown_and_friendly_413_page(app, login):
    adm = login('admin', 'adminpass')
    page = adm.get('/admin/products/add').data.decode()
    assert 'up to 30 MB in total' in page and '16 MB' not in page and 'image/webp' in page
    promo_form = adm.get('/admin/promotions/add').data.decode()
    assert 'Max size: 30 MB' in promo_form and '16MB' not in promo_form

    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
    big = io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * (2 * 1024 * 1024))
    r = adm.post('/admin/products/add', data={'name': 'Too big', 'price': '100', 'category': 'Electronics', 'stock': '1',
                                              'photos': [(big, 'huge.png')]}, content_type='multipart/form-data')
    assert r.status_code == 413
    body = r.data.decode()
    assert 'That file is too big - the limit is 1 MB' in body and 'Back to Home' in body


def webp_file():
    out = io.BytesIO()
    Image.new('RGB', (64, 64), (10, 200, 90)).save(out, 'WEBP')
    out.seek(0)
    return out


def test_product_photos_can_be_webp(app, login, db):
    adm = login('admin', 'adminpass')
    base = {'name': 'WebP Phone', 'price': '1000', 'category': 'Electronics', 'stock': '5', 'description': 'x'}
    adm.post('/admin/products/add', data={**base, 'photos': [(webp_file(), 'front.webp'), (fake_png(), 'back.png')]},
             content_type='multipart/form-data')
    pid, main = db().execute("SELECT id, image FROM products WHERE name='WebP Phone'").fetchone()
    try:
        assert main.endswith('_front.webp') and os.path.isfile(os.path.join(app.static_folder, main))
        # the content is checked, not just the name
        r = adm.post(f'/admin/products/edit/{pid}', data={**base, 'photos': [(io.BytesIO(b'not an image at all'), 'fake.webp')]},
                     content_type='multipart/form-data', follow_redirects=True)
        assert b'is not a PNG, JPG, GIF or WebP image' in r.data
        assert db().execute('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid,)).fetchone()[0] == 1
    finally:
        adm.post(f'/admin/products/delete/{pid}')
    assert not os.path.exists(os.path.join(app.static_folder, main))


def test_low_stock_threshold_comes_from_environment(app):
    assert app.config['LOW_STOCK_THRESHOLD'] == int(os.environ.get('LOW_STOCK_THRESHOLD') or 5)
    env = {**os.environ, 'LOW_STOCK_THRESHOLD': '9'}
    out = subprocess.run([sys.executable, '-c', 'from shop.config import Config; print(Config.LOW_STOCK_THRESHOLD)'],
                         cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == '9'


def test_low_stock_threshold_used_by_dashboard(app, login):
    app.config['LOW_STOCK_THRESHOLD'] = 2
    page = login('admin', 'adminpass').get('/admin/dashboard').data.decode()
    assert '2 or fewer left' in page


def test_promo_banner_shows_no_unverified_discount(app, db, login):
    c = db()
    c.execute("INSERT INTO promotions (title, original_price, promo_price, discount_text, is_active) "
              "VALUES ('Big kettle sale', 10000, 5000, NULL, 1)")
    c.commit()
    page = app.test_client().get('/').data.decode()
    assert 'Big kettle sale' in page and 'KSh 5,000' in page
    assert '-50%' not in page and 'promo-was' not in page and 'KSh 10,000' not in page

    form = login('admin', 'adminpass').get('/admin/promotions/add').data.decode()
    assert 'Not shown to customers' in form and 'It must be true' in form


@pytest.mark.skipif(not PG_URL, reason='needs TEST_DATABASE_URL (PostgreSQL)')
def test_copy_to_postgres_keeps_history_deals_and_proofs(tmp_path):
    from shop import create_app
    from shop.cli import copy_to_postgres_command
    from shop.db import direct_connection

    # an old shop on SQLite
    src_path = str(tmp_path / 'old.db')
    create_app({'TESTING': True, 'SECRET_KEY': 'x', 'DATABASE_PATH': src_path, 'DATABASE_URL': ''})
    s = direct_connection(database_path=src_path)
    s.execute("INSERT INTO users (id, username, email, password_hash) VALUES (1, 'jane', 'jane@example.com', 'x')")
    s.execute("INSERT INTO products (id, name, price, original_price, stock, was_price_verified) VALUES (1, 'Kettle', 1500, 2000, 5, 1)")
    s.execute("INSERT INTO products (id, name, price, stock) VALUES (2, 'Old product', 700, 5)")
    s.execute("INSERT INTO price_history (product_id, price, original_price, changed_at) VALUES (1, 2000, NULL, '2026-01-01 08:00:00')")
    s.execute("INSERT INTO price_history (product_id, price, original_price, changed_at) VALUES (1, 1500, 2000, '2026-03-01 08:00:00')")
    s.execute("INSERT INTO group_deals (id, product_id, title, tiers, ends_at) VALUES (1, 1, 'Kettle deal', '[[1, 1500]]', '2099-01-01 00:00:00')")
    s.execute("INSERT INTO orders (id, user_id, total_amount, status) VALUES (1, 1, 1500, 'Processing')")
    s.execute("INSERT INTO order_items (order_id, product_id, quantity, price, deal_id) VALUES (1, 1, 1, 1500, 1)")
    s.execute("INSERT INTO carts (user_id, product_id, quantity, deal_id) VALUES (1, 1, 1, 1)")
    s.execute("INSERT INTO order_proofs (order_id, kind, path, uploaded_by) VALUES (1, 'photo', 'abc_box.jpg', 1)")
    s.commit(); s.close()

    c = direct_connection(PG_URL)
    c.execute('DROP SCHEMA public CASCADE'); c.execute('CREATE SCHEMA public'); c.commit(); c.close()
    pg_app = create_app({'TESTING': True, 'SECRET_KEY': 'x', 'DATABASE_URL': PG_URL, 'DATABASE_PATH': str(tmp_path / 'unused.db')})
    result = pg_app.test_cli_runner().invoke(copy_to_postgres_command, [src_path])
    assert result.exit_code == 0, result.output
    assert 'static/uploads' in result.output and 'private_uploads' in result.output

    d = direct_connection(PG_URL)
    try:
        history = d.execute('SELECT price, changed_at FROM price_history WHERE product_id = 1 ORDER BY changed_at').fetchall()
        assert [float(r[0]) for r in history] == [2000, 1500] and str(history[0][1]).startswith('2026-01-01')
        assert d.execute('SELECT COUNT(*) FROM price_history WHERE product_id = 2').fetchone()[0] == 1   # seeded
        assert d.execute('SELECT title FROM group_deals').fetchone()[0] == 'Kettle deal'
        assert d.execute('SELECT deal_id FROM order_items').fetchone()[0] == 1
        assert d.execute('SELECT deal_id FROM carts').fetchone()[0] == 1
        row = d.execute('SELECT kind, path FROM order_proofs').fetchone()
        assert (row[0], row[1]) == ('photo', 'abc_box.jpg')
        # numbering continues after the copied ids
        d.execute("INSERT INTO order_proofs (order_id, kind, path) VALUES (1, 'video', 'v.mp4')")
        d.execute('INSERT INTO price_history (product_id, price) VALUES (2, 650)')
        d.rollback()
    finally:
        d.close()


def test_uploaded_photos_are_re_encoded(app, login, db):
    """A file that only starts like a picture is refused; real photos lose hidden camera/GPS data."""
    import io
    import os
    from PIL import Image
    adm = login('admin', 'adminpass')
    base = {'price': '100', 'category': 'Electronics', 'stock': '3', 'description': 'x'}
    polyglot = io.BytesIO(b'GIF89a' + b'\x00' * 10 + b'<script>alert(1)</script>')
    adm.post('/admin/products/add', data={**base, 'name': 'Poly', 'photos': [(polyglot, 'poly.png')]}, content_type='multipart/form-data')
    assert db().execute("SELECT image FROM products WHERE name='Poly'").fetchone()[0] == 'uploads/default.jpg'
    exif = Image.Exif(); exif[0x010F] = 'PhoneMaker'
    photo = io.BytesIO(); Image.new('RGB', (50, 40), (10, 20, 30)).save(photo, 'JPEG', exif=exif); photo.seek(0)
    adm.post('/admin/products/add', data={**base, 'name': 'Camera', 'photos': [(photo, 'cam.jpg')]}, content_type='multipart/form-data')
    path = db().execute("SELECT image FROM products WHERE name='Camera'").fetchone()[0]
    full = os.path.join(app.static_folder, path)
    try:
        with Image.open(full) as im:
            assert im.format == 'JPEG' and not im.getexif()
    finally:
        os.remove(full)
