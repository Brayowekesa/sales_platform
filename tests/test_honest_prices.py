"""Honest prices: discounts must be real, price history, price-drop and back-in-stock alerts."""
from shop.extensions import mail


def edit_product(admin, pid, price, original='', stock=10):
    return admin.post(f'/admin/products/edit/{pid}', data={'name': 'Kettle', 'price': str(price), 'original_price': str(original),
                                                          'category': 'Home & Kitchen', 'stock': str(stock), 'description': 'Steel kettle'},
                      content_type='multipart/form-data')


def backdate_history(db, pid, days):
    """Pretend the current price has been in force for `days` days."""
    c = db()
    if c.raw.__class__.__module__.startswith('sqlite3'):
        c.execute(f"UPDATE price_history SET changed_at = datetime('now', '-{days} days') WHERE product_id = ?", (pid,))
    else:
        c.execute(f"UPDATE price_history SET changed_at = now() AT TIME ZONE 'UTC' - interval '{days} days' WHERE product_id = ?", (pid,))
    c.commit()


def test_fake_discount_is_not_shown(app, login, db):
    admin = login('admin', 'adminpass')
    # Kettle (id 5) has always sold at 2,199; claiming "was 3,500" must not show a discount
    edit_product(admin, 5, 2199, 3500)
    page = app.test_client().get('/product/5').data
    assert b'3,500' not in page and b'Checked discount' not in page
    assert b'Not shown yet' in admin.get('/admin/products/edit/5').data
    home = app.test_client().get('/').data.decode()
    assert 'KSh 3,500' not in home


def test_real_discount_is_shown_with_history(app, login, db):
    admin = login('admin', 'adminpass')
    backdate_history(db, 5, 20)                  # sold at 2,199 for 20 days
    edit_product(admin, 5, 1799, 2199)           # now a real discount
    page = app.test_client().get('/product/5').data
    assert b'Checked discount: it really sold at KSh 2,199' in page and b'-18%' in page
    assert b'Price history' in page and b'Lowest in 90 days' in page
    assert b'Discount shown to customers' in admin.get('/admin/products/edit/5').data


def test_discount_stops_after_60_days(app, login, db):
    admin = login('admin', 'adminpass')
    backdate_history(db, 5, 20)
    edit_product(admin, 5, 1799, 2199)
    c = db(); c.execute("UPDATE products SET discount_started_at = '2020-01-01 00:00:00' WHERE id = 5"); c.commit()
    assert b'Checked discount' not in app.test_client().get('/product/5').data
    assert b'over 60 days' in admin.get('/admin/products/edit/5').data


def test_new_product_cannot_launch_with_a_discount(login, db):
    admin = login('admin', 'adminpass')
    admin.post('/admin/products/add', data={'name': 'Fresh Item', 'price': '900', 'original_price': '1500', 'category': 'Electronics', 'stock': '5'},
               content_type='multipart/form-data')
    assert db().execute("SELECT was_price_verified FROM products WHERE name='Fresh Item'").fetchone()[0] == 0


def test_price_drop_and_back_in_stock_alerts(app, login, db):
    jane = login('jane', 'janepass')
    jane.post('/wishlist/add/5')
    admin = login('admin', 'adminpass')
    with mail.record_messages() as outbox:
        edit_product(admin, 5, 1999)             # price drop
    assert len(outbox) == 1 and 'Price drop' in outbox[0].subject and 'KSh 1,999' in outbox[0].body and 'KSh 2,199' in outbox[0].body
    with mail.record_messages() as outbox:
        edit_product(admin, 5, 1899)             # second drop the same day: no second email
    assert len(outbox) == 0
    c = db(); c.execute("DELETE FROM rate_limit_events"); c.commit()
    edit_product(admin, 5, 1899, stock=0)
    with mail.record_messages() as outbox:
        edit_product(admin, 5, 1899, stock=4)    # back in stock
    assert len(outbox) == 1 and 'Back in stock' in outbox[0].subject


def test_honest_prices_page(app):
    page = app.test_client().get('/honest-prices').data
    assert b'at least 14 days' in page and b'after 60 days' in page
    assert b'/honest-prices' in app.test_client().get('/').data        # linked from the footer
