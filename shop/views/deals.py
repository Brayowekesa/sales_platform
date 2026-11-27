"""Pamoja deals: public deal pages (shareable on WhatsApp) and joining a deal."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from shop.db import get_db_connection
from shop.deals import close_due_deals, deal_info, open_deals, utc_now_str

bp = Blueprint('deals', __name__)


@bp.route('/deals')
def deal_list():
    close_due_deals()
    uid = current_user.id if current_user.is_authenticated else None
    with get_db_connection() as conn:
        deals = []
        for row in open_deals(conn):
            info = deal_info(conn, row, uid)
            info.update(product_name=row['product_name'], product_image=row['product_image'], normal_price=row['normal_price'])
            deals.append(info)
    return render_template('deals/list.html', deals=deals, title='Pamoja deals')


@bp.route('/deals/<int:deal_id>')
def deal_page(deal_id):
    close_due_deals()
    uid = current_user.id if current_user.is_authenticated else None
    with get_db_connection() as conn:
        row = conn.execute('''SELECT d.*, p.name AS product_name, p.image AS product_image, p.price AS normal_price,
                                     p.description AS product_description, p.stock
                              FROM group_deals d JOIN products p ON p.id = d.product_id WHERE d.id = ?''', (deal_id,)).fetchone()
        if not row:
            abort(404)
        deal = deal_info(conn, row, uid)
    deal.update(product_name=row['product_name'], product_image=row['product_image'], normal_price=row['normal_price'],
                product_description=row['product_description'], stock=row['stock'])
    return render_template('deals/deal.html', deal=deal, title=deal['title'] or f"Pamoja deal: {row['product_name']}")


@bp.route('/deals/<int:deal_id>/join', methods=['POST'])
@login_required
def join_deal(deal_id):
    try:
        quantity = int(request.form.get('quantity', 1))
    except (TypeError, ValueError):
        quantity = 1
    with get_db_connection() as conn:
        deal = conn.execute('''SELECT d.id, d.product_id, d.max_per_buyer, p.stock FROM group_deals d JOIN products p ON p.id = d.product_id
                               WHERE d.id = ? AND d.status = 'active' AND d.ends_at > ?''', (deal_id, utc_now_str())).fetchone()
        if not deal:
            flash('Sorry, this Pamoja deal has ended.', 'info')
            return redirect(url_for('deals.deal_page', deal_id=deal_id))
        quantity = max(1, min(quantity, deal['max_per_buyer'], deal['stock']))
        if deal['stock'] < 1:
            flash('Sorry, this item is sold out.', 'warning')
            return redirect(url_for('deals.deal_page', deal_id=deal_id))
        existing = conn.execute('SELECT id FROM carts WHERE user_id = ? AND product_id = ?', (current_user.id, deal['product_id'])).fetchone()
        if existing:
            conn.execute('UPDATE carts SET quantity = ?, deal_id = ? WHERE id = ?', (quantity, deal_id, existing['id']))
        else:
            conn.execute('INSERT INTO carts (user_id, product_id, quantity, deal_id) VALUES (?, ?, ?, ?)',
                         (current_user.id, deal['product_id'], quantity, deal_id))
    flash("You're in! Finish your order to lock your place in the deal.", 'success')
    return redirect(url_for('orders.checkout'))
