"""Flask extensions, created here and attached to the app in create_app()."""
from flask import current_app
from flask_bootstrap import Bootstrap
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf import CSRFProtect
from itsdangerous import URLSafeTimedSerializer

csrf = CSRFProtect()
mail = Mail()
bootstrap = Bootstrap()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'


def token_serializer():
    """Signs email-confirmation and password-reset tokens with the app's secret key."""
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
