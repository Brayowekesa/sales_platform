"""Admin: shop settings and delivery options."""
import re
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from shop.db import get_db_connection
from shop.forms import DeliveryZoneForm, ShopSettingsForm
from shop.helpers import SHOP_SETTING_DEFAULTS, SHOP_SETTING_FIELDS
from shop.security import admin_required, log_activity

from shop.admin import bp


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    form = ShopSettingsForm()
    if form.validate_on_submit():
        errors = []
        cleaned = {}
        for key, label, _ in SHOP_SETTING_FIELDS:
            value = (request.form.get(key) or '').strip()[:300]
            if key in ('map_url', 'facebook', 'instagram', 'tiktok') and value and not re.match(r'^https://', value):
                errors.append(f'{label} must start with https://')
            if key == 'returns_days' and value and not value.isdigit():
                errors.append('Return window must be a number of days.')
            if key == 'email' and value and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', value):
                errors.append('Enter a valid email address.')
            cleaned[key] = value
        if errors:
            for e in errors:
                flash(e, 'danger')
        else:
            with get_db_connection() as conn:
                conn.executemany('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                                 list(cleaned.items()))
                conn.commit()
            log_activity("Admin updated shop settings", user_id=current_user.id)
            flash('Shop settings saved.', 'success')
            return redirect(url_for('admin.admin_settings'))
    with get_db_connection() as conn:
        saved = {r['key']: r['value'] for r in conn.execute('SELECT key, value FROM settings')}
    return render_template('admin/settings.html', form=form, fields=SHOP_SETTING_FIELDS, saved=saved,
                           defaults=SHOP_SETTING_DEFAULTS, title='Shop settings')


def _zone_values(form):
    return (form.name.data.strip(), form.kind.data, form.fee.data or 0, form.free_over.data or None,
            (form.eta.data or '').strip() or None, (form.details.data or '').strip() or None,
            form.sort_order.data or 0, 1 if form.is_active.data else 0)


@bp.route('/delivery', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_delivery():
    form = DeliveryZoneForm()
    if form.validate_on_submit():
        with get_db_connection() as conn:
            conn.execute('INSERT INTO delivery_zones (name, kind, fee, free_over, eta, details, sort_order, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         _zone_values(form))
            conn.commit()
        log_activity("Admin added delivery option", user_id=current_user.id, details=form.name.data)
        flash('Delivery option added.', 'success')
        return redirect(url_for('admin.admin_delivery'))
    with get_db_connection() as conn:
        zones = conn.execute('SELECT * FROM delivery_zones ORDER BY sort_order, id').fetchall()
    return render_template('admin/delivery.html', zones=zones, form=form, editing=None, title='Delivery & Pickup')


@bp.route('/delivery/<int:zone_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_delivery(zone_id):
    with get_db_connection() as conn:
        zone = conn.execute('SELECT * FROM delivery_zones WHERE id = ?', (zone_id,)).fetchone()
    if not zone:
        flash('Delivery option not found.', 'danger')
        return redirect(url_for('admin.admin_delivery'))
    form = DeliveryZoneForm(data={**dict(zone), 'is_active': bool(zone['is_active'])}) if request.method == 'GET' else DeliveryZoneForm()
    if form.validate_on_submit():
        with get_db_connection() as conn:
            conn.execute('UPDATE delivery_zones SET name=?, kind=?, fee=?, free_over=?, eta=?, details=?, sort_order=?, is_active=? WHERE id=?',
                         _zone_values(form) + (zone_id,))
            conn.commit()
        log_activity("Admin edited delivery option", user_id=current_user.id, details=f"ID {zone_id}: {form.name.data}")
        flash('Delivery option saved.', 'success')
        return redirect(url_for('admin.admin_delivery'))
    with get_db_connection() as conn:
        zones = conn.execute('SELECT * FROM delivery_zones ORDER BY sort_order, id').fetchall()
    return render_template('admin/delivery.html', zones=zones, form=form, editing=zone, title='Delivery & Pickup')


@bp.route('/delivery/<int:zone_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_delivery(zone_id):
    with get_db_connection() as conn:
        conn.execute('DELETE FROM delivery_zones WHERE id = ?', (zone_id,))
        conn.commit()
    log_activity("Admin deleted delivery option", user_id=current_user.id, details=f"ID {zone_id}")
    flash('Delivery option deleted. Past orders keep their delivery details.', 'success')
    return redirect(url_for('admin.admin_delivery'))
