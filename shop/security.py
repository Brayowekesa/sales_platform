"""Security: safe redirects, rate limiting, upload checks, admin-only pages, security headers, activity log."""
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse
from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user
from shop.db import get_db_connection
from shop.extensions import login_manager


def is_safe_next_url(target):
    """Only allow redirects to paths on this site (blocks ?next=https://evil.example)."""
    if not target:
        return False
    if '\\' in target or any(ord(c) < 32 or ord(c) == 127 for c in target):
        return False                    # browsers treat '/\\evil.example' like '//evil.example'
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith('/') and not target.startswith('//')


def same_site_referrer():
    """The page the request came from, but only if it is on this site."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return ref
    return None


@login_manager.unauthorized_handler
def handle_needs_login():
    """Guest tried something that needs an account (add to cart, wishlist, checkout...)."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': False, 'login_required': True, 'message': 'Please log in to continue.'}), 401
    flash('Please log in to continue.', 'info')
    if request.method == 'GET':
        next_url = request.full_path.rstrip('?')
    else:
        # A form was posted (e.g. the wishlist heart): come back to the page it was on, not the POST address
        ref = same_site_referrer()
        parsed = urlparse(ref) if ref else None
        next_url = (parsed.path + (f'?{parsed.query}' if parsed.query else '')) if parsed else None
    return redirect(url_for('auth.login', next=next_url))


CSP = "; ".join([
    "default-src 'self'",
    # inline <script>/<style> blocks are still used by the templates
    "script-src 'self' 'unsafe-inline' https://code.jquery.com https://cdn.jsdelivr.net https://stackpath.bootstrapcdn.com",
    "style-src 'self' 'unsafe-inline' https://stackpath.bootstrapcdn.com https://cdnjs.cloudflare.com https://fonts.googleapis.com https://cdn.jsdelivr.net",
    "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:",
    "img-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'self'",
    "object-src 'none'",
])


def set_security_headers(response):
    response.headers.setdefault('Content-Security-Policy', CSP)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')     # don't guess file types
    response.headers.setdefault('X-Frame-Options', 'DENY')               # no embedding in other sites (clickjacking)
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(self)')
    if current_app.config['SESSION_COOKIE_SECURE']:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    # Pages for logged-in users must not be stored by shared caches
    if current_user.is_authenticated and not request.path.startswith('/static/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


def client_ip():
    # Behind a proxy (e.g. nginx) configure werkzeug's ProxyFix so this is the real visitor IP
    return request.remote_addr or 'unknown'


def utc_minutes_ago(minutes):
    """'YYYY-MM-DD HH:MM:SS' in UTC, comparable with CURRENT_TIMESTAMP values on SQLite and PostgreSQL."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')


def rate_limited(kind, key, limit, window_minutes):
    """True if `key` already has `limit` or more `kind` events in the last `window_minutes`."""
    with get_db_connection() as conn:
        n = conn.execute(
            'SELECT COUNT(*) FROM rate_limit_events WHERE kind = ? AND key = ? AND created_at > ?',
            (kind, key, utc_minutes_ago(window_minutes))).fetchone()[0]
    return n >= limit


def record_rate_event(kind, key):
    with get_db_connection() as conn:
        conn.execute('INSERT INTO rate_limit_events (kind, key) VALUES (?, ?)', (kind, key))
        # keep the table small
        conn.execute('DELETE FROM rate_limit_events WHERE created_at < ?', (utc_minutes_ago(24 * 60),))
        conn.commit()


def clear_rate_events(kind, key):
    with get_db_connection() as conn:
        conn.execute('DELETE FROM rate_limit_events WHERE kind = ? AND key = ?', (kind, key))
        conn.commit()


LOGIN_MAX_FAILS_PER_ACCOUNT = 5     # per username/email, per 15 minutes


LOGIN_MAX_FAILS_PER_IP = 20         # per visitor IP, per 15 minutes


LOGIN_LOCK_MINUTES = 15


IMAGE_SIGNATURES = (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a')


def is_real_image(file_storage):
    """Check the file's first bytes really are PNG/JPEG/GIF (not just the file name)."""
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    return any(head.startswith(sig) for sig in IMAGE_SIGNATURES)


def valid_image_upload(file_storage):
    return allowed_file(file_storage.filename) and is_real_image(file_storage)


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


def log_activity(action, user_id=None, details=None): # Modified
    # Using app.app_context() here is good practice if get_db_connection might need app config
    # or if you plan to call this from outside a request context sometimes.
    # For calls within a request, direct get_db_connection should be fine if it's simple.
    try:
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO activity_logs (user_id, action, details) VALUES (?, ?, ?)',
                (user_id, action, details)
            )
            conn.commit()
    except Exception as e:
        # Log this error to Flask logger to avoid crashing if logging itself fails
        # Make sure 'app' is defined if using current_app.logger. You might need current_app.logger
        # from flask import current_app
        # current_app.logger.error(f"Failed to log activity: {action}, Error: {e}")
        print(f"CRITICAL: Failed to log activity: {action}, Error: {e}") # Fallback to print


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Unauthorized access. Admin privileges required.', 'danger')
            return redirect(url_for('store.index'))
        return f(*args, **kwargs)
    return decorated_function
