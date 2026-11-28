"""Information pages, favicon/robots, the installable-app files and error pages."""
import json

from flask import Blueprint, current_app, render_template, request, url_for
from shop.db import get_db_connection
from shop.helpers import active_delivery_zones
from shop.security import log_activity


bp = Blueprint('pages', __name__)


@bp.app_errorhandler(404)
def not_found_error(error):
    # You can log the 404 error here if desired, e.g., log which path was not found
    # log_activity("404 Not Found", details=f"Path: {request.path}") # If you want to log 404s
    return render_template('errors/404.html', title="Page Not Found"), 404


@bp.app_errorhandler(413)
def too_large_error(error):
    """An upload bigger than MAX_CONTENT_LENGTH (e.g. many photos or a long video at once)."""
    from shop.helpers import max_upload_mb
    from shop.security import same_site_referrer
    return render_template('errors/413.html', title="File Too Big", limit_mb=max_upload_mb(),
                           back_url=same_site_referrer()), 413


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


@bp.route('/img/<size>/<path:path>')
def image(size, path):
    """Resized WebP copy of an uploaded photo (cached on disk and in the browser)."""
    from flask import abort, send_file
    from shop.images import cached_copy
    out = cached_copy(path, size)
    if not out:
        abort(404)
    response = send_file(out, mimetype='image/webp', max_age=30 * 86400)
    response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
    return response


@bp.route('/data-saver/<state>')
def data_saver(state):
    """Turn data-saver mode on/off for this browser (remembered for a year)."""
    from flask import make_response, redirect
    from shop.security import is_safe_next_url
    nxt = request.args.get('next')
    response = make_response(redirect(nxt if is_safe_next_url(nxt) else url_for('store.index')))
    if state == 'on':
        response.set_cookie('data_saver', '1', max_age=365 * 86400, samesite='Lax', secure=current_app.config['SESSION_COOKIE_SECURE'])
    else:
        response.delete_cookie('data_saver')
    return response


@bp.route('/favicon.ico')
def favicon():
    # Browsers ask for /favicon.ico directly
    return current_app.send_static_file('favicon.ico')


@bp.route('/manifest.webmanifest')
def manifest():
    # Lets phones install the shop like an app (Add to Home screen)
    name = current_app.config.get('SITE_NAME', 'E-Shop')
    data = {
        'name': name, 'short_name': name[:12], 'start_url': '/?source=app', 'scope': '/',
        'display': 'standalone', 'background_color': '#ffffff', 'theme_color': '#0b2545',
        'description': f'Shop {name} - delivery across Kenya, pay with M-Pesa or cash.',
        'icons': [
            {'src': url_for('static', filename='images/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': url_for('static', filename='images/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
    }
    response = current_app.response_class(json.dumps(data), mimetype='application/manifest+json')
    response.cache_control.max_age = 86400
    return response


@bp.route('/sw.js')
def service_worker():
    # Served from the root so it can look after every page
    response = current_app.send_static_file('js/sw.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    response.cache_control.no_cache = True
    response.cache_control.max_age = 0
    return response


@bp.route('/offline')
def offline():
    return render_template('offline.html')


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
