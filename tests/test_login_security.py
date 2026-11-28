"""Changing the password or two-step login logs out every other device."""
import re

import pyotp


def logged_in(client):
    return client.get('/cart').status_code == 200


def test_password_reset_logs_out_everywhere(app, login, db):
    phone = login('jane', 'janepass')
    laptop = app.test_client()
    laptop.post('/login', data={'login_identifier': 'jane', 'password': 'janepass', 'remember': 'y'})
    assert logged_in(phone) and logged_in(laptop)
    from shop.notifications import _password_fingerprint
    from shop.extensions import token_serializer
    with app.test_request_context():
        h = db().execute("SELECT password_hash FROM users WHERE username='jane'").fetchone()[0]
        token = token_serializer().dumps(['Jane@Example.com', _password_fingerprint(h)], salt='password-reset')
    app.test_client().post(f'/reset_password/{token}', data={'password': 'NewPass123!', 'confirm_password': 'NewPass123!'})
    assert db().execute("SELECT session_version FROM users WHERE username='jane'").fetchone()[0] == 1
    assert not logged_in(phone) and not logged_in(laptop)
    # the "remember me" cookie alone doesn't bring the old login back
    laptop2 = app.test_client()
    cookie = laptop.get_cookie('remember_token')
    if cookie:
        laptop2.set_cookie('remember_token', cookie.value)
        assert not logged_in(laptop2)


def test_turning_on_two_step_keeps_this_device_only(app, login):
    phone = login('jane', 'janepass')
    laptop = login('jane', 'janepass')
    phone.get('/account/two-step/setup')
    with phone.session_transaction() as s:
        secret = s['2fa_setup_secret']
    page = phone.post('/account/two-step/setup', data={'code': pyotp.TOTP(secret).now()}).data
    assert re.findall(rb'<li><code>([0-9a-f-]+)</code></li>', page)
    assert logged_in(phone) and not logged_in(laptop)


def test_admin_password_reset_logs_member_out(app, login, db):
    jane = login('jane', 'janepass')
    uid = db().execute("SELECT id FROM users WHERE username='jane'").fetchone()[0]
    admin = login('admin', 'adminpass')
    admin.post(f'/admin/members/reset-password/{uid}', data={'new_password': 'Another123!', 'confirm_password': 'Another123!'})
    assert not logged_in(jane)


def test_old_style_session_is_logged_out(app, login):
    jane = login('jane', 'janepass')
    with jane.session_transaction() as s:
        s['_user_id'] = '2'                       # session from before this change
    assert not logged_in(jane)
