"""Registration, email verification, login limits and password resets."""
from shop.extensions import token_serializer
from shop.notifications import _password_fingerprint


def register(client, username='newbie', email='NewBie@Example.com', password='pw123456'):
    return client.post('/register', data={'username': username, 'email': email, 'password': password, 'confirm_password': password})


def test_verification_code_limits_and_case_insensitive_email(app, db):
    g = app.test_client(); register(g)
    code = db().execute("SELECT verification_code FROM users WHERE username='newbie'").fetchone()[0]
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(4):
        g.post('/verify_email', data={'email': 'newbie@example.com', 'verification_code': wrong})
    assert db().execute("SELECT verification_attempts FROM users WHERE username='newbie'").fetchone()[0] == 4
    g.post('/verify_email', data={'email': 'newbie@example.com', 'verification_code': wrong})
    new_code, attempts = db().execute("SELECT verification_code, verification_attempts FROM users WHERE username='newbie'").fetchone()
    assert new_code != code and attempts == 0                      # 5th wrong try replaces the code
    g.post('/verify_email', data={'email': 'NEWBIE@example.com', 'verification_code': new_code})
    assert db().execute("SELECT confirmed FROM users WHERE username='newbie'").fetchone()[0] == 1
    r = app.test_client().post('/login', data={'login_identifier': 'NewBie@EXAMPLE.com', 'password': 'pw123456'})
    assert r.status_code == 302 and '/verify' not in r.headers['Location']


def test_resend_does_not_reveal_accounts(app):
    r = app.test_client().post('/verify_email/resend', data={'email': 'nobody@nowhere.com'}, follow_redirects=True)
    assert b'If that account is waiting' in r.data


def test_signup_rules(app):
    assert b'at least 8 characters' in register(app.test_client(), 'shorty', 'shorty@x.com', '1234567').data
    assert b'Use only letters, numbers' in register(app.test_client(), "x');alert(1);//", 'x@x.com').data


def test_login_lockout_per_account_and_reset(app, db, login):
    bad = app.test_client()
    for _ in range(5):
        bad.post('/login', data={'login_identifier': 'jane', 'password': 'wrong'})
    r = bad.post('/login', data={'login_identifier': 'jane', 'password': 'janepass'})
    assert b'Too many failed login attempts' in r.data
    assert app.test_client().post('/login', data={'login_identifier': 'admin', 'password': 'adminpass'}).status_code == 302
    c = db(); c.execute('DELETE FROM rate_limit_events'); c.commit()          # lock expired
    assert app.test_client().post('/login', data={'login_identifier': 'Jane@example.com', 'password': 'janepass'}).status_code == 302


def test_successful_login_resets_failure_count(app):
    cl = app.test_client()
    for _ in range(4):
        cl.post('/login', data={'login_identifier': 'jane', 'password': 'wrong'})
    cl.post('/login', data={'login_identifier': 'jane', 'password': 'janepass'}); cl.post('/logout')
    for _ in range(4):
        cl.post('/login', data={'login_identifier': 'jane', 'password': 'wrong'})
    assert cl.post('/login', data={'login_identifier': 'jane', 'password': 'janepass'}).status_code == 302


def test_login_lockout_per_network(app):
    ip = app.test_client()
    for i in range(20):
        ip.post('/login', data={'login_identifier': f'nobody{i}', 'password': 'x'})
    assert b'Too many failed login attempts' in ip.post('/login', data={'login_identifier': 'admin', 'password': 'adminpass'}).data


def test_password_reset_link_single_use_and_rate_limited(app, db):
    ph = db().execute("SELECT password_hash FROM users WHERE username='jane'").fetchone()[0]
    with app.test_request_context():
        tok = token_serializer().dumps(['jane@example.com', _password_fingerprint(ph)], salt='password-reset')
    t = app.test_client()
    t.post(f'/reset_password/{tok}', data={'password': 'newpass123', 'confirm_password': 'newpass123'})
    assert b'already been used' in t.get(f'/reset_password/{tok}', follow_redirects=True).data
    assert b'If an account with that email exists' in t.post('/reset_password', data={'email': 'ghost@nowhere.com'}, follow_redirects=True).data
    rr = app.test_client()
    for _ in range(3):
        rr.post('/reset_password', data={'email': 'jane@example.com'})
    assert b'Too many reset requests' in rr.post('/reset_password', data={'email': 'jane@example.com'}, follow_redirects=True).data
