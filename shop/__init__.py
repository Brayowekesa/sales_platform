"""ESA E-Shop - a Flask online shop.

create_app() builds the application; app.py at the project root calls it, so
`flask --app app run` keeps working.
"""
import os
import secrets

from dotenv import load_dotenv

load_dotenv()  # read .env before the settings below are loaded

from flask import Flask  # noqa: E402

from shop.config import BASE_DIR, Config  # noqa: E402


def create_app(overrides=None):
    app = Flask(__name__,
                template_folder=os.path.join(BASE_DIR, 'templates'),
                static_folder=os.path.join(BASE_DIR, 'static'))
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    if not app.config.get('SECRET_KEY') and app.config.get('SESSION_COOKIE_SECURE') and not app.testing:
        # Running for real (HTTPS) without a fixed key: logins, payment callbacks and rider/gift links
        # would break between server workers and restarts. Refuse to start instead.
        raise RuntimeError('FLASK_SECRET_KEY must be set in production (see docs/OPERATIONS.md).')
    if not app.config.get('SECRET_KEY'):
        # Safe, but everyone is logged out on restart and several server workers won't share sessions.
        app.config['SECRET_KEY'] = secrets.token_hex(32)
        print("WARNING: FLASK_SECRET_KEY is not set - using a temporary random key (sessions reset on restart).")

    if app.config.get('SENTRY_DSN') and not app.testing:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.flask import FlaskIntegration
            sentry_sdk.init(dsn=app.config['SENTRY_DSN'], integrations=[FlaskIntegration()],
                            send_default_pii=False, traces_sample_rate=0.0)
        except ImportError:
            print('WARNING: SENTRY_DSN is set but sentry-sdk is not installed.')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PROMOTIONS_UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PROOF_FOLDER'], exist_ok=True)

    # --- extensions ---
    from shop.extensions import csrf, login_manager, mail
    csrf.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    # Importing these registers the user loader and the "please log in" handler with Flask-Login
    from shop.models import load_user_from_session as load_user
    from shop.security import handle_needs_login
    assert load_user and handle_needs_login

    # --- templates: globals, filters, shared values, security headers ---
    from shop.helpers import localtime_filter, money_filter, product_img, safe_link_filter, utility_processor
    from shop.security import set_security_headers
    from shop.pricing import honest_discount
    app.jinja_env.globals.update(SITE_NAME=app.config['SITE_NAME'], honest_discount=honest_discount,
                                 CURRENCY_SYMBOL=app.config['CURRENCY_SYMBOL'],
                                 product_img=product_img)
    app.jinja_env.filters.update(money=money_filter, localtime=localtime_filter, safe_link=safe_link_filter)
    app.context_processor(utility_processor)
    app.after_request(set_security_headers)

    # --- pages ---
    from shop.admin import bp as admin_bp
    from shop.views import account, auth, cart, deals, orders, pages, store
    for bp in (store.bp, cart.bp, orders.bp, auth.bp, account.bp, pages.bp, deals.bp):
        app.register_blueprint(bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # --- command line: flask --app app init-db / create-admin / copy-to-postgres ---
    from shop.cli import close_deals_command, copy_to_postgres_command, create_admin_command, init_db_command, reset_2fa_command
    app.cli.add_command(close_deals_command)
    app.cli.add_command(reset_2fa_command)
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(copy_to_postgres_command)

    # --- behind a reverse proxy (Caddy/nginx): trust its X-Forwarded-* headers ---
    if app.config['TRUSTED_PROXY_COUNT']:
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = app.config['TRUSTED_PROXY_COUNT']
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n)   # not the Host header: links use PUBLIC_URL

    # --- make sure the database tables exist ---
    if app.config['AUTO_MIGRATE']:
        from shop.db import init_db
        with app.app_context():
            init_db()

    return app
