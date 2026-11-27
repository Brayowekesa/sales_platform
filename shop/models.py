"""The logged-in user."""
from flask_login import UserMixin
from werkzeug.security import check_password_hash
from shop.db import get_db_connection
from shop.extensions import login_manager


class User(UserMixin):
    def __init__(self, id, username, email, password_hash, is_admin=0, confirmed=0, totp_enabled=0):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.confirmed = confirmed
        self.totp_enabled = bool(totp_enabled)   # two-step login turned on

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as conn:
        user_data = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_data:
            return User(
                id=user_data['id'],
                username=user_data['username'],
                email=user_data['email'],
                password_hash=user_data['password_hash'],
                is_admin=user_data['is_admin'],
                confirmed=user_data['confirmed'],
                totp_enabled=user_data['totp_enabled'],
            )
        return None
