"""Fixes from the security assessment and penetration test."""
import pytest

from shop import create_app
from shop.extensions import mail
from shop.security import is_safe_next_url


def zone_id(db, name):
    return str(db().execute('SELECT id FROM delivery_zones WHERE name=?', (name,)).fetchone()[0])


def test_redirects_stay_on_this_site(app):
    with app.test_request_context():
        for bad in ('//evil.example', '/\\evil.example', '/\\/evil.example', 'https://evil.example', '/x\ny', ''):
            assert not is_safe_next_url(bad)
        assert is_safe_next_url('/orders?page=2')


def test_registering_with_a_used_email_does_not_reveal_it(app, db):
    with mail.record_messages() as outbox:
        r = app.test_client().post('/register', data={'username': 'newperson', 'email': 'jane@example.com',
                                                      'password': 'Password123', 'confirm_password': 'Password123'},
                                   follow_redirects=True)
    assert b'Registration successful' in r.data and b'already registered' not in r.data
    assert len(outbox) == 1 and 'already have' in outbox[0].subject and outbox[0].recipients == ['jane@example.com']
    assert db().execute("SELECT COUNT(*) FROM users WHERE username='newperson'").fetchone()[0] == 0


def test_verify_page_does_not_reveal_accounts_and_limits_emails(app, db):
    client = app.test_client()
    page = client.post('/verify_email', data={'email': 'nobody@example.com', 'verification_code': '123456'}).data
    assert b'not right' in page and b'No account' not in page
    c = db()
    c.execute("INSERT INTO users(username,email,password_hash,confirmed,verification_code,verification_code_expires) "
              "VALUES('newbie','newbie@example.com','x',0,'111111','2099-01-01 00:00:00')")
    c.commit()
    with mail.record_messages() as outbox:
        for _ in range(40):                                              # keep guessing (new codes get emailed)
            client.post('/verify_email', data={'email': 'newbie@example.com', 'verification_code': '000000'})
    assert len(outbox) <= 5                                              # at most 5 code emails a day
    page = client.post('/verify_email', data={'email': 'newbie@example.com', 'verification_code': '000000'}).data
    assert b'Too many' in page or b'several codes' in page


def test_new_recovery_codes_need_the_password(app, login, db):
    jane = login('jane', 'janepass')
    c = db(); c.execute("UPDATE users SET totp_enabled=1, totp_secret='JBSWY3DPEHPK3PXP', recovery_codes='[]' WHERE username='jane'"); c.commit()
    r = jane.post('/account/two-step/new-codes', data={'password': 'wrong'}, follow_redirects=True)
    assert b'password is not right' in r.data
    r = jane.post('/account/two-step/new-codes', data={'password': 'janepass'})
    assert b'Save your recovery codes' in r.data or b'recovery' in r.data.lower()


def test_log_out_of_all_devices(app, login):
    phone, laptop = login('jane', 'janepass'), login('jane', 'janepass')
    phone.post('/account/logout-everywhere')
    assert phone.get('/cart').status_code == 302 and laptop.get('/cart').status_code == 302


def test_huge_page_numbers_do_not_crash(app):
    for page in ('99999999999999999999', '-5', '0'):
        assert app.test_client().get(f'/?page={page}').status_code == 200


def test_only_buyers_can_review(app, login, db):
    jane = login('jane', 'janepass')
    jane.post('/product/3/review', data={'rating': '5', 'comment': 'Fake praise'})
    assert db().execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0
    jane.post('/cart/add/3', data={'quantity': 1})
    jane.post('/checkout', data={'contact_name': 'Jane', 'contact_phone': '0712345678', 'delivery_zone': zone_id(db, 'Nairobi CBD'),
                                 'shipping_address': 'Moi Ave', 'payment_method': 'Cash on Delivery'})
    c = db(); c.execute("UPDATE orders SET status='Delivered'"); c.commit()
    jane.post('/product/3/review', data={'rating': '5', 'comment': 'Really good tee'})
    page = app.test_client().get('/product/3').data
    assert b'Really good tee' in page and b'Verified buyer' in page


def test_email_links_use_public_url_not_host_header(app, db):
    app.config['PUBLIC_URL'] = 'https://shop.example.co.ke'
    with mail.record_messages() as outbox:
        app.test_client().post('/reset_password', data={'email': 'jane@example.com'}, headers={'Host': 'evil.example'})
    assert outbox and 'https://shop.example.co.ke/reset_password/' in outbox[0].body and 'evil.example' not in outbox[0].body


def test_production_refuses_to_start_without_secret_key(tmp_path, monkeypatch):
    monkeypatch.delenv('FLASK_SECRET_KEY', raising=False)
    with pytest.raises(RuntimeError):
        create_app({'SECRET_KEY': None, 'SESSION_COOKIE_SECURE': True, 'DATABASE_PATH': str(tmp_path / 'x.db'),
                    'DATABASE_URL': '', 'AUTO_MIGRATE': False})
