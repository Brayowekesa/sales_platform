"""Settings, read from environment variables (or a .env file). See .env.example."""
import os
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env(name, default=None):
    """An environment variable, treating empty values as 'not set'."""
    value = os.environ.get(name)
    return value if value not in (None, '') else default


class Config:
    # SECRET_KEY signs sessions and password-reset tokens. Set FLASK_SECRET_KEY in production
    # (generate one with: python -c "import secrets; print(secrets.token_hex(32))").
    SECRET_KEY = env('FLASK_SECRET_KEY')

    SITE_NAME = env('SITE_NAME', 'ESA E-Shop')
    CURRENCY_SYMBOL = env('CURRENCY_SYMBOL', 'KSh')
    SHOP_TIMEZONE = env('SHOP_TIMEZONE', 'Africa/Nairobi')

    DATABASE_PATH = env('DATABASE_PATH', os.path.join(BASE_DIR, 'sales.db'))
    # PostgreSQL for production, e.g. postgresql://shop:password@db:5432/shop (empty = use the SQLite file above)
    DATABASE_URL = env('DATABASE_URL', '')
    DB_POOL_SIZE = int(env('DB_POOL_SIZE', 10))
    # Run database migrations when the app starts (the Docker setup sets 0 and migrates once before starting workers)
    AUTO_MIGRATE = env('AUTO_MIGRATE', '1') == '1'
    # Number of reverse proxies in front of the app (Caddy/nginx = 1). Needed for real visitor IPs and https links.
    TRUSTED_PROXY_COUNT = int(env('TRUSTED_PROXY_COUNT', 0))
    # Browsers may cache CSS/JS/images for a day
    SEND_FILE_MAX_AGE_DEFAULT = 86400
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    PROMOTIONS_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'promotions')
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB per request

    # Cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = env('SESSION_COOKIE_SECURE', '0') == '1'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    ALLOWED_REGISTER_EMAILS = {e.strip().lower() for e in env('ALLOWED_REGISTER_EMAILS', '').split(',') if e.strip()}

    # Email - any SMTP provider (see .env.example)
    MAIL_SERVER = env('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(env('MAIL_PORT', 587))
    MAIL_USE_TLS = env('MAIL_USE_TLS', '1') == '1'
    MAIL_USE_SSL = env('MAIL_USE_SSL', '0') == '1'
    MAIL_USERNAME = env('MAIL_USERNAME', '')
    MAIL_PASSWORD = env('MAIL_APP_PASSWORD', '')
    MAIL_DEFAULT_SENDER = env('MAIL_DEFAULT_SENDER') or env('MAIL_USERNAME') or None

    # M-Pesa (Daraja)
    MPESA_CONSUMER_KEY = env('MPESA_CONSUMER_KEY', 'your_consumer_key')
    MPESA_CONSUMER_SECRET = env('MPESA_CONSUMER_SECRET', 'your_consumer_secret')
    MPESA_BUSINESS_SHORT_CODE = env('MPESA_BUSINESS_SHORT_CODE', '174379')
    MPESA_PASSKEY = env('MPESA_PASSKEY', 'your_mpesa_passkey')
    MPESA_CALLBACK_URL = env('MPESA_CALLBACK_URL', 'https://yourdomain.com/mpesa_callback')
