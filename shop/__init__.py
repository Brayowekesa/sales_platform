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

    if not app.config.get('SECRET_KEY'):
        # Safe, but everyone is logged out on restart and several server workers won't share sessions.
        app.config['SECRET_KEY'] = secrets.token_hex(32)
        print("WARNING: FLASK_SECRET_KEY is not set - using a temporary random key (sessions reset on restart).")

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PROMOTIONS_UPLOAD_FOLDER'], exist_ok=True)

    # --- extensions ---
    from shop.extensions import bootstrap, csrf, login_manager, mail
    csrf.init_app(app)
    mail.init_app(app)
    bootstrap.init_app(app)
    login_manager.init_app(app)
    from shop import models  # noqa: F401  (registers the user loader)
    from shop.security import handle_needs_login  # noqa: F401  (registers the "please log in" handler)

    # --- templates: globals, filters, shared values, security headers ---
    from shop.helpers import localtime_filter, money_filter, product_img, safe_link_filter, utility_processor
    from shop.security import set_security_headers
    app.jinja_env.globals.update(SITE_NAME=app.config['SITE_NAME'],
                                 CURRENCY_SYMBOL=app.config['CURRENCY_SYMBOL'],
                                 product_img=product_img)
    app.jinja_env.filters.update(money=money_filter, localtime=localtime_filter, safe_link=safe_link_filter)
    app.context_processor(utility_processor)
    app.after_request(set_security_headers)

    # --- pages ---
    from shop.admin import bp as admin_bp
    from shop.views import auth, cart, orders, pages, store
    for bp in (store.bp, cart.bp, orders.bp, auth.bp, pages.bp):
        app.register_blueprint(bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # --- command line: flask --app app init-db / create-admin ---
    from shop.cli import create_admin_command, init_db_command
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)

    # --- make sure the database tables exist ---
    from shop.db import init_db
    with app.app_context():
        init_db()

    return app
