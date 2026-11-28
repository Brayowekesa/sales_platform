"""Old/missing form tokens get a friendly 'try again', and the local secret key survives restarts."""
import os
from types import SimpleNamespace

from shop import _local_secret_key


def test_expired_form_goes_back_with_a_message(app):
    app.config['WTF_CSRF_ENABLED'] = True                     # real CSRF checks for this test
    client = app.test_client()
    r = client.post('/login', data={'login_identifier': 'jane', 'password': 'janepass'},
                    headers={'Referer': 'http://localhost/login'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert b'please try again' in client.get('/login').data
    r = client.post('/cart/add/3', data={'quantity': 1}, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code in (400, 401, 302)                  # AJAX gets JSON (or the login prompt), never a bare HTML error
    if r.status_code == 400:
        assert r.json['success'] is False


def test_expired_form_from_another_site_goes_home(app):
    app.config['WTF_CSRF_ENABLED'] = True
    r = app.test_client().post('/login', data={}, headers={'Referer': 'https://evil.example/'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/')


def test_local_secret_key_is_kept_between_restarts(tmp_path):
    fake_app = SimpleNamespace(instance_path=str(tmp_path / 'instance'))
    first = _local_secret_key(fake_app)
    assert len(first) == 64 and _local_secret_key(fake_app) == first
    assert oct(os.stat(tmp_path / 'instance' / 'secret_key').st_mode)[-3:] == '600'
