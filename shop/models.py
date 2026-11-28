"""The logged-in user."""
from flask import request
from flask_login import UserMixin, login_user
from werkzeug.security import check_password_hash
from shop.db import get_db_connection
from shop.extensions import login_manager


class User(UserMixin):
    def __init__(self, id, username, email, password_hash, is_admin=0, confirmed=0, totp_enabled=0, session_version=0):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.confirmed = confirmed
        self.totp_enabled = bool(totp_enabled)   # two-step login turned on
        self.session_version = session_version or 0

    def get_id(self):
        # Stored in the session and the "remember me" cookie. Raising session_version (password
        # changed, two-step login turned on/off) makes every older login stop working.
        return f'{self.id}.{self.session_version}'

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)


def load_user(user_id):
    """The user with this database id (or None)."""
    with get_db_connection() as conn:
        user_data = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user_data:
        return None
    return User(
        id=user_data['id'],
        username=user_data['username'],
        email=user_data['email'],
        password_hash=user_data['password_hash'],
        is_admin=user_data['is_admin'],
        confirmed=user_data['confirmed'],
        totp_enabled=user_data['totp_enabled'],
        session_version=user_data['session_version'],
    )


@login_manager.user_loader
def load_user_from_session(token):
    """Session/cookie value 'id.version' -> user, only if the version is still current."""
    try:
        user_id, version = (int(part) for part in str(token).split('.'))
    except ValueError:
        return None                      # old-style or tampered value: log in again
    user = load_user(user_id)
    return user if user and user.session_version == version else None


def end_other_sessions(conn, user_id):
    """Log the user out everywhere (call inside the transaction that changes their password/2FA)."""
    conn.execute('UPDATE users SET session_version = COALESCE(session_version, 0) + 1 WHERE id = ?', (user_id,))


def stay_logged_in(user_id):
    """After end_other_sessions() for the current user: keep THIS device logged in."""
    remember = bool(request.cookies.get('remember_token'))
    login_user(load_user(user_id), remember=remember)
