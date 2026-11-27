"""Information pages, favicon/robots and error pages."""
from flask import Blueprint, current_app, render_template
from shop.db import get_db_connection
from shop.helpers import active_delivery_zones
from shop.security import log_activity


bp = Blueprint('pages', __name__)


@bp.app_errorhandler(404)
def not_found_error(error):
    # You can log the 404 error here if desired, e.g., log which path was not found
    # log_activity("404 Not Found", details=f"Path: {request.path}") # If you want to log 404s
    return render_template('errors/404.html', title="Page Not Found"), 404


@bp.app_errorhandler(500)
def internal_error(error):
    # It's good practice to rollback the DB session in case the error was DB related
    # This depends on how you manage your DB sessions. If using 'with get_db_connection() as conn:'
    # extensively and the error happened outside such a block, an explicit rollback isn't always
    # automatically handled by just being in an errorhandler. However, often the request
    # context teardown might handle it. For robustness with SQLite and manual connection patterns:
    # try:
    #     db = getattr(g, '_database', None)
    #     if db is not None:
    #         db.rollback()
    # except Exception as e:
    #     current_app.logger.error(f"Error during rollback in 500 handler: {e}")

    log_activity("500 Internal Server Error", details=str(error)) # Log the error
    current_app.logger.error(f"Server Error: {error}", exc_info=True) # Log with traceback
    return render_template('errors/500.html', title="Server Error"), 500


@bp.route('/healthz')
def healthz():
    """For Docker/uptime monitors: 200 if the app and database respond."""
    try:
        with get_db_connection() as conn:
            conn.execute('SELECT 1').fetchone()
        return {'status': 'ok'}
    except Exception as e:
        current_app.logger.error(f'Health check failed: {e}')
        return {'status': 'error'}, 503


@bp.route('/favicon.ico')
def favicon():
    # Browsers ask for /favicon.ico directly
    return current_app.send_static_file('favicon.ico')


@bp.route('/robots.txt')
def robots_txt():
    body = "User-agent: *\nDisallow: /admin/\nDisallow: /cart\nDisallow: /checkout\nDisallow: /orders\nDisallow: /wishlist\n"
    return current_app.response_class(body, mimetype='text/plain')


@bp.route('/contact')
def page_contact():
    return render_template('pages/contact.html', title='Contact us')


@bp.route('/delivery-info')
def page_delivery():
    return render_template('pages/delivery.html', zones=active_delivery_zones(), title='Delivery & pickup')


@bp.route('/honest-prices')
def page_honest_prices():
    from shop.pricing import MAX_SALE_DAYS, VERIFY_DAYS
    return render_template('pages/honest_prices.html', verify_days=VERIFY_DAYS, max_sale_days=MAX_SALE_DAYS, title='Honest prices')


@bp.route('/returns')
def page_returns():
    return render_template('pages/returns.html', title='Returns & refunds')


@bp.route('/privacy')
def page_privacy():
    return render_template('pages/privacy.html', title='Privacy policy')


@bp.route('/terms')
def page_terms():
    return render_template('pages/terms.html', title='Terms of service')
