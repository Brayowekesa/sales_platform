"""Admin: Pamoja group deals (create, extend, end)."""
import json
from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from shop.admin import bp
from shop.db import get_db_connection
from shop.deals import TierError, close_deal, close_due_deals, deal_info, default_end, parse_tiers, tiers_text, utc_now_str
from shop.helpers import SHOP_TZ, to_local
from shop.security import admin_required, log_activity


def _local_input_to_utc(value):
    """'2026-12-01T18:00' typed in shop time -> UTC 'YYYY-MM-DD HH:MM:SS' (None if invalid)."""
    try:
        local = datetime.strptime(value, '%Y-%m-%dT%H:%M').replace(tzinfo=SHOP_TZ)
    except (TypeError, ValueError):
        return None
    return local.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


@bp.route('/deals', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_deals():
    close_due_deals()
    form = {'product_id': '', 'title': '', 'tiers': '', 'max_per_buyer': '3',
            'ends_at': to_local(default_end().strftime('%Y-%m-%d %H:%M:%S')).strftime('%Y-%m-%dT%H:%M')}
    errors = []
    with get_db_connection() as conn:
        products = conn.execute('SELECT id, name, price, stock FROM products ORDER BY name').fetchall()
        if request.method == 'POST':
            form.update({k: (request.form.get(k) or '').strip() for k in form})
            product = next((p for p in products if str(p['id']) == form['product_id']), None)
            tiers = None
            if not product:
                errors.append('Choose a product.')
            else:
                try:
                    tiers = parse_tiers(form['tiers'], product['price'])
                except TierError as e:
                    errors.append(str(e))
                if conn.execute("SELECT 1 FROM group_deals WHERE product_id = ? AND status = 'active'", (product['id'],)).fetchone():
                    errors.append('This product already has a running deal.')
            ends_at = _local_input_to_utc(form['ends_at'])
            if not ends_at or ends_at <= utc_now_str():
                errors.append('Choose an end date and time in the future.')
            try:
                max_per_buyer = int(form['max_per_buyer'])
                if not 1 <= max_per_buyer <= 20:
                    raise ValueError
            except ValueError:
                errors.append('Max per person must be between 1 and 20.')
            if not errors:
                conn.execute('INSERT INTO group_deals (product_id, title, tiers, max_per_buyer, ends_at) VALUES (?, ?, ?, ?, ?)',
                             (product['id'], form['title'][:120] or None, json.dumps(tiers), max_per_buyer, ends_at))
                conn.commit()
                log_activity('Admin created Pamoja deal', user_id=current_user.id,
                             details=f"{product['name']}: {tiers_text(tiers)} until {ends_at} UTC")
                flash('Pamoja deal started. Share it on WhatsApp from the deal page!', 'success')
                return redirect(url_for('admin.admin_deals'))
        rows = conn.execute('''SELECT d.*, p.name AS product_name FROM group_deals d JOIN products p ON p.id = d.product_id
                               ORDER BY CASE WHEN d.status = 'active' THEN 0 ELSE 1 END, d.ends_at DESC''').fetchall()
        deals = []
        for r in rows:
            info = deal_info(conn, r)
            info['product_name'] = r['product_name']
            info['refunds_due'] = conn.execute('''SELECT COUNT(*), COALESCE(SUM(deal_refund_due), 0) FROM orders
                                                  WHERE deal_refund_due > 0 AND id IN (SELECT order_id FROM order_items WHERE deal_id = ?)''',
                                               (r['id'],)).fetchone()
            deals.append(info)
    return render_template('admin/deals.html', deals=deals, products=products, form=form, errors=errors, title='Pamoja deals')


@bp.route('/deals/<int:deal_id>/end', methods=['POST'])
@login_required
@admin_required
def admin_end_deal(deal_id):
    result = close_deal(deal_id, who=current_user.username)
    if result:
        final, orders = result
        log_activity('Admin ended Pamoja deal', user_id=current_user.id, details=f'Deal {deal_id}: final KSh {final}, {len(orders)} orders')
        flash(f'Deal ended at KSh {final:,.0f}. {len(orders)} order(s) updated and customers told - you can now send them.', 'success')
    else:
        flash('This deal has already ended.', 'info')
    return redirect(url_for('admin.admin_deals'))


@bp.route('/deals/<int:deal_id>/extend', methods=['POST'])
@login_required
@admin_required
def admin_extend_deal(deal_id):
    ends_at = _local_input_to_utc(request.form.get('ends_at'))
    with get_db_connection() as conn:
        deal = conn.execute("SELECT ends_at FROM group_deals WHERE id = ? AND status = 'active'", (deal_id,)).fetchone()
        current = deal and (deal['ends_at'] if isinstance(deal['ends_at'], str) else deal['ends_at'].strftime('%Y-%m-%d %H:%M:%S'))
        if not deal:
            flash('This deal has already ended.', 'info')
        elif not ends_at or ends_at <= max(current, utc_now_str()):
            flash('The new end time must be later than the current one (deals can only be extended).', 'warning')
        else:
            conn.execute('UPDATE group_deals SET ends_at = ? WHERE id = ?', (ends_at, deal_id))
            conn.commit()
            log_activity('Admin extended Pamoja deal', user_id=current_user.id, details=f'Deal {deal_id} until {ends_at} UTC')
            flash('Deal extended.', 'success')
    return redirect(url_for('admin.admin_deals'))
