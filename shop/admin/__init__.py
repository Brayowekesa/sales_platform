"""Admin panel (/admin/...). Every page here requires an admin account."""
from flask import Blueprint, flash, redirect, url_for
from flask_login import current_user

bp = Blueprint('admin', __name__)


@bp.before_request
def require_admin():
    # Second line of defence: each view also has @admin_required, but a new admin page
    # added without it is still protected.
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=None))
    if not current_user.is_admin:
        flash('Unauthorized access. Admin privileges required.', 'danger')
        return redirect(url_for('store.index'))


from shop.admin import catalog, dashboard, people, sales, settings  # noqa: E402,F401  (register the admin pages)
