"""Two-step login (authenticator app), admin enforcement, M-Pesa callback IP allowlist."""
import re
import time

import pyotp

from shop.payments import mpesa_callback_signature


def turn_on_2fa(client):
    """Go through setup like a user would; returns (secret, recovery codes)."""
    client.get('/account/two-step/setup')
    with client.session_transaction() as s:
        secret = s['2fa_setup_secret']
    page = client.post('/account/two-step/setup', data={'code': pyotp.TOTP(secret).now()}).data
    codes = re.findall(rb'<li><code>([0-9a-f-]+)</code></li>', page)
    return secret, [c.decode() for c in codes]


def next_code(secret, steps=1):
    """The authenticator code for a later 30-second step (each code can only be used once)."""
    return pyotp.TOTP(secret).at(time.time() + 30 * steps)


def password_step(app, username='jane', password='janepass', next_url=None):
    c = app.test_client()
    r = c.post('/login' + (f'?next={next_url}' if next_url else ''), data={'login_identifier': username, 'password': password})
    return c, r


def test_setup_then_login_needs_code(app, login, db):
    jane = login('jane', 'janepass')
    assert b'Turn on two-step login' in jane.get('/account/security').data
    secret, codes = turn_on_2fa(jane)
    assert len(codes) == 8
    assert db().execute("SELECT totp_enabled FROM users WHERE username='jane'").fetchone()[0] == 1

    c, r = password_step(app, next_url='/wishlist')
    assert r.headers['Location'].endswith('/login/code')
    assert '/login' in c.get('/cart').headers['Location']                    # not logged in yet
    r = c.post('/login/code', data={'code': next_code(secret)})
    assert r.headers['Location'].endswith('/wishlist')                        # back to where they were going
    assert c.get('/cart').status_code == 200
    # the same code can't be used again (e.g. by someone who saw it over the shoulder)
    c2, _ = password_step(app)
    assert b'not right' in c2.post('/login/code', data={'code': next_code(secret)}).data


def test_wrong_codes_lock_out(app, login):
    secret, _ = turn_on_2fa(login('jane', 'janepass'))
    c, _ = password_step(app)
    for _ in range(5):
        assert b'not right' in c.post('/login/code', data={'code': '000000'}).data
    r = c.post('/login/code', data={'code': pyotp.TOTP(secret).now()}, follow_redirects=True)
    assert b'Too many wrong codes' in r.data                                  # even the right code is refused now
    assert '/login' in c.get('/cart').headers['Location']


def test_recovery_code_works_once(app, login):
    _, codes = turn_on_2fa(login('jane', 'janepass'))
    c, _ = password_step(app)
    assert c.post('/login/code', data={'code': codes[0].upper()}).status_code == 302   # case/dashes don't matter
    assert c.get('/cart').status_code == 200
    c2, _ = password_step(app)
    assert b'not right' in c2.post('/login/code', data={'code': codes[0]}).data
    assert b'Recovery codes left: <strong>7</strong>' in c.get('/account/security').data


def test_code_step_expires(app, login):
    secret, _ = turn_on_2fa(login('jane', 'janepass'))
    c, _ = password_step(app)
    with c.session_transaction() as s:
        s['2fa_pending'] = {**s['2fa_pending'], 'at': int(time.time()) - 600}   # password entered 10 minutes ago
    r = c.post('/login/code', data={'code': pyotp.TOTP(secret).now()})
    assert r.headers['Location'].endswith('/login')


def test_turning_off_needs_password_and_code(app, login, db):
    jane = login('jane', 'janepass')
    secret, _ = turn_on_2fa(jane)
    jane.post('/account/two-step/disable', data={'password': 'wrong', 'code': pyotp.TOTP(secret).now()})
    assert db().execute("SELECT totp_enabled FROM users WHERE username='jane'").fetchone()[0] == 1
    jane.post('/account/two-step/disable', data={'password': 'janepass', 'code': next_code(secret)})
    assert db().execute("SELECT totp_enabled, totp_secret FROM users WHERE username='jane'").fetchone() == (0, None)


def test_admins_must_use_two_step_when_required(app, login):
    app.config['REQUIRE_ADMIN_2FA'] = True
    admin = login('admin', 'adminpass')
    assert admin.get('/admin/dashboard').headers['Location'].endswith('/account/two-step/setup')
    secret, _ = turn_on_2fa(admin)
    assert admin.get('/admin/dashboard').status_code == 200


def test_reset_2fa_command(app, login, db):
    turn_on_2fa(login('jane', 'janepass'))
    result = app.test_cli_runner().invoke(args=['reset-2fa', 'jane'])
    assert result.exit_code == 0
    assert db().execute("SELECT totp_enabled FROM users WHERE username='jane'").fetchone()[0] == 0
    assert app.test_cli_runner().invoke(args=['reset-2fa', 'nobody']).exit_code != 0


def test_mpesa_callback_ip_allowlist(app, db):
    c = db()
    c.execute("INSERT INTO orders(user_id,total_amount,status,payment_method,transaction_id) VALUES(2,100,'Pending Payment','Lipa na M-Pesa','ws_1')")
    c.commit()
    oid = c.execute('SELECT MAX(id) FROM orders').fetchone()[0]
    with app.app_context():
        sig = mpesa_callback_signature(str(oid))
    body = {'Body': {'stkCallback': {'ResultCode': 0, 'CheckoutRequestID': 'ws_1',
                                     'CallbackMetadata': {'Item': [{'Name': 'Amount', 'Value': 100}]}}}}
    app.config['MPESA_CALLBACK_ALLOWED_IPS'] = ['196.201.214.0/24']        # test client comes from 127.0.0.1
    assert app.test_client().post(f'/mpesa_callback?order_id={oid}&sig={sig}', json=body).status_code == 403
    app.config['MPESA_CALLBACK_ALLOWED_IPS'] = ['196.201.214.0/24', '127.0.0.1']
    assert app.test_client().post(f'/mpesa_callback?order_id={oid}&sig={sig}', json=body).status_code == 200
    assert db().execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()[0] == 'Paid'
