"""Admin: orders."""
from shop.delivery import rider_token, MAX_PROOF_FILES, MAX_VIDEO_MB, classify_proof_upload, delete_proof_file, new_delivery_pin, save_proof_file
from shop.payments import confirm_delivery_with_pin
from datetime import datetime
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from shop.deals import close_due_deals, open_deal_ids_for_order
from shop.db import DatabaseError, get_db_connection
from shop.notifications import notify_order_event, sms_enabled
from shop.payments import CheckoutError, STOCK_RELEASED_STATUSES, record_order_status, release_order_stock, reserve_order_stock
from shop.security import admin_required, log_activity

from shop.admin import bp


@bp.route('/orders')
@login_required
@admin_required
def admin_orders():
    close_due_deals()
    page = request.args.get('page', 1, type=int)
    per_page = 15 # Number of orders per page
    status_filter = request.args.get('status', None)


    # Initialize lists and counts
    orders = []
    total_orders_count = 0
    total_pages = 0
    distinct_statuses = ['All'] # Default with 'All'
    pagination_query_params = {}

    try:
        with get_db_connection() as conn:
            # Base SQL queries
            base_count_sql = 'SELECT COUNT(o.id) FROM orders o JOIN users u ON o.user_id = u.id'
            base_select_sql = '''SELECT o.id, u.username, o.order_date, o.total_amount, o.status, o.payment_method 
                                 FROM orders o JOIN users u ON o.user_id = u.id'''

            sql_filter_params = [] # Parameters for the SQL WHERE clause

            # Filtering logic
            where_conditions = []
            if status_filter and status_filter != 'All':
                where_conditions.append('o.status = ?')
                sql_filter_params.append(status_filter)
                pagination_query_params['status'] = status_filter # For pagination links

            where_clause_str = ""
            if where_conditions:
                where_clause_str = ' WHERE ' + ' AND '.join(where_conditions)

            # Construct final SQL queries
            final_count_sql = base_count_sql + where_clause_str
            final_select_sql = base_select_sql + where_clause_str + ' ORDER BY o.order_date DESC LIMIT ? OFFSET ?'


            # Execute count query
            total_orders_data = conn.execute(final_count_sql, tuple(sql_filter_params)).fetchone()
            total_orders_count = total_orders_data[0] if total_orders_data and total_orders_data[0] is not None else 0
            

            # Calculate offset for pagination
            offset = (page - 1) * per_page
            
            # Parameters for the main select query (filter params + pagination params)
            select_query_full_params = tuple(sql_filter_params + [per_page, offset])

            # Execute select query for orders
            raw_orders = conn.execute(final_select_sql, select_query_full_params).fetchall()
            # if raw_orders: # DEBUG - Print first fetched order if any
            #     print(f"First raw_order content: {dict(raw_orders[0])}")

            for row in raw_orders:
                order_dict = dict(row)
                if order_dict.get('order_date') and isinstance(order_dict['order_date'], str):
                    try:
                        order_dict['order_date'] = datetime.strptime(order_dict['order_date'], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        log_msg = f"Could not parse order_date string: {order_dict['order_date']} for order ID: {order_dict['id']}"
                        if hasattr(current_app, 'logger'): current_app.logger.warning(log_msg)
                        else: print(f"WARNING: {log_msg}")
                orders.append(order_dict)
            
            total_pages = (total_orders_count + per_page - 1) // per_page if total_orders_count > 0 else 0
            
            # Get distinct statuses for the filter dropdown
            distinct_statuses_rows = conn.execute('SELECT DISTINCT status FROM orders WHERE status IS NOT NULL ORDER BY status').fetchall()
            distinct_statuses.extend([row['status'] for row in distinct_statuses_rows])

    except DatabaseError as e:
        error_msg = f"Database error in admin_orders: {e}"
        if hasattr(current_app, 'logger'): current_app.logger.error(error_msg)
        else: print(f"ERROR: {error_msg}")
        flash("A database error occurred while retrieving orders.", "danger")
        # Fallback to empty lists so template doesn't break
        orders = []
        total_orders_count = 0
        total_pages = 0
        # distinct_statuses will have 'All'
    except Exception as e: # Catch any other unexpected errors
        error_msg = f"An unexpected error occurred in admin_orders: {e}"
        if hasattr(current_app, 'logger'): current_app.logger.error(error_msg, exc_info=True) # Log with traceback
        else: print(f"ERROR: {error_msg}")
        flash("An unexpected error occurred. Please try again.", "danger")
        orders = []
        total_orders_count = 0
        total_pages = 0


    return render_template(
        'admin/orders.html',
        orders=orders,
        title='Manage Orders',
        current_page=page,
        total_pages=total_pages,
        endpoint='admin.admin_orders',
        query_params=pagination_query_params, # Used to build pagination links correctly with filters
        distinct_statuses=list(dict.fromkeys(distinct_statuses)), # Ensure 'All' is first and statuses are unique
        current_status_filter=status_filter if status_filter else 'All'
    )


@bp.route('/orders/<int:order_id>')
@login_required
@admin_required
def admin_order_details(order_id):
    close_due_deals()
    with get_db_connection() as conn:
        raw_order = conn.execute('''
            SELECT o.*, u.username, u.email
            FROM orders o JOIN users u ON o.user_id = u.id
            WHERE o.id = ?
        ''', (order_id,)).fetchone()

        if not raw_order:
            flash('Order not found.', 'danger')
            return redirect(url_for('admin.admin_orders'))

        # ---- START MODIFICATION ----
        order = dict(raw_order) # Convert the row to a mutable dictionary
        if order.get('order_date') and isinstance(order['order_date'], str):
            try:
                # Attempt to parse the datetime string. Adjust format if necessary.
                # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                order['order_date'] = datetime.strptime(order['order_date'], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                current_app.logger.warning(f"Could not parse order_date string: {order['order_date']} for order ID: {order['id']}")
                # Optionally, set to None so the template logic `if order.order_date` still works
                # order['order_date'] = None # Or keep as string
        # ---- END MODIFICATION ----

        history = conn.execute(
            'SELECT status, note, created_at FROM order_status_history WHERE order_id = ? ORDER BY created_at, id',
            (order_id,)).fetchall()
        proofs = conn.execute('SELECT id, kind, created_at FROM order_proofs WHERE order_id = ? ORDER BY id', (order_id,)).fetchall()
        deals_open = open_deal_ids_for_order(conn, order_id)
        order_items = conn.execute('''
            SELECT oi.quantity, oi.price, p.name, p.image, p.id as product_id
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (order_id,)).fetchall()

    rider_link = url_for('orders.rider_delivery', token=rider_token(order_id), _external=True) \
        if order.get('status') == 'Shipped' and order.get('delivery_method') != 'pickup' else None
    return render_template('admin/order_details.html', order=order, order_items=order_items, history=history,
                           open_deal=deals_open[0] if deals_open else None,
                           proofs=proofs, rider_link=rider_link, max_proof_files=MAX_PROOF_FILES,
                           sms_enabled=sms_enabled(), title=f'Order #{order_id}')


@bp.route('/orders/update_status/<int:order_id>', methods=['POST'])
@login_required
@admin_required
def admin_update_order_status(order_id):
    # CSRF token validation is implicitly handled by Flask-WTF if this was a WTForm submission.
    # Since it's a simple form in the template, ensure your template includes the CSRF token
    # and your main app setup has CSRFProtect(app).
    new_status = request.form.get('status')
    # Consider defining valid_statuses more globally or in current_app.config
    valid_statuses = ['Pending', 'Pending Payment', 'Processing', 'Paid', 'Shipped', 'Delivered', 'Cancelled', 'Refunded', 'Payment Failed'] 
    
    if new_status not in valid_statuses:
        flash('Invalid order status selected.', 'danger')
        return redirect(url_for('admin.admin_order_details', order_id=order_id))
    try:
        with get_db_connection() as conn:
            order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                flash('Order not found.', 'danger')
                return redirect(url_for('admin.admin_orders'))
            old_status = order['status']
            pin_row = conn.execute('SELECT delivery_pin, delivered_with_pin FROM orders WHERE id = ?', (order_id,)).fetchone()
            override_reason = (request.form.get('override_reason') or '').strip()[:200]
            if new_status in ('Shipped', 'Delivered') and open_deal_ids_for_order(conn, order_id):
                flash("This order is in a Pamoja deal that hasn't ended yet - the final price isn't known. "
                      "End the deal (Admin > Pamoja deals) or wait for it to finish, then send it.", 'warning')
                return redirect(url_for('admin.admin_order_details', order_id=order_id))
            if new_status == 'Delivered' and old_status != 'Delivered' and pin_row['delivery_pin'] and not override_reason:
                flash("Enter the customer's delivery PIN below, or give a reason for marking it delivered without the PIN.", 'warning')
                return redirect(url_for('admin.admin_order_details', order_id=order_id))
            was_released = old_status in STOCK_RELEASED_STATUSES
            now_released = new_status in STOCK_RELEASED_STATUSES
            if now_released and not was_released:
                release_order_stock(conn, order_id)   # cancelled: items go back on sale
            elif was_released and not now_released:
                reserve_order_stock(conn, order_id)   # reopened: take the items out of stock again
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
            if new_status == 'Shipped' and old_status != 'Shipped':
                # Customer gets a PIN to give the rider (or show at pickup) only once they have the parcel
                conn.execute('UPDATE orders SET delivery_pin = ?, delivered_with_pin = 0 WHERE id = ?', (new_delivery_pin(), order_id))
            if new_status != old_status:
                note = (request.form.get('note') or '').strip()[:200] or None
                if new_status == 'Delivered' and override_reason:
                    note = f'Marked delivered without PIN by {current_user.username}: {override_reason}'
                record_order_status(conn, order_id, new_status, note)
            conn.commit()
        flash(f'Order #{order_id} status updated to {new_status}.', 'success')
        if new_status != old_status:
            notify_order_event(order_id, 'status')
        log_activity("Admin updated order status", user_id=current_user.id, details=f"Order ID: {order_id}, New Status: {new_status}")
    except CheckoutError as ce:
        flash(str(ce), 'warning')
    except Exception as e:
        current_app.logger.error(f"Error updating order status for {order_id}: {e}")
        flash(f'Error updating order status: {str(e)}', 'danger')
        log_activity("Admin update order status failed", user_id=current_user.id, details=f"Order ID: {order_id}, Error: {str(e)}")
    return redirect(url_for('admin.admin_order_details', order_id=order_id))


# --- Packing proof and delivery confirmation ---
@bp.route('/orders/<int:order_id>/proof', methods=['POST'])
@login_required
@admin_required
def admin_upload_proof(order_id):
    files = [f for f in request.files.getlist('proof') if f and f.filename]
    with get_db_connection() as conn:
        if not conn.execute('SELECT 1 FROM orders WHERE id = ?', (order_id,)).fetchone():
            flash('Order not found.', 'danger')
            return redirect(url_for('admin.admin_orders'))
        existing = conn.execute('SELECT COUNT(*) FROM order_proofs WHERE order_id = ?', (order_id,)).fetchone()[0]
    saved, skipped = 0, []
    for f in files:
        if existing + saved >= MAX_PROOF_FILES:
            skipped.append(f'{f.filename} (limit of {MAX_PROOF_FILES} files)')
            continue
        kind = classify_proof_upload(f)
        if not kind:
            skipped.append(f'{f.filename} (not a photo or video)')
            continue
        f.stream.seek(0, 2); size = f.stream.tell(); f.stream.seek(0)
        if kind == 'video' and size > MAX_VIDEO_MB * 1024 * 1024:
            skipped.append(f'{f.filename} (videos up to {MAX_VIDEO_MB} MB)')
            continue
        name = save_proof_file(f)
        with get_db_connection() as conn:
            conn.execute('INSERT INTO order_proofs (order_id, kind, path, uploaded_by) VALUES (?, ?, ?, ?)',
                         (order_id, kind, name, current_user.id))
        saved += 1
    for s in skipped:
        flash(f'Not added: {s}', 'warning')
    if saved:
        with get_db_connection() as conn:
            # a new proof asks the customer again
            conn.execute('UPDATE orders SET proof_response = NULL, proof_response_note = NULL, proof_response_at = NULL WHERE id = ?', (order_id,))
            record_order_status(conn, order_id, 'Packing proof added', f'{saved} file(s) for you to check')
        log_activity('Admin added packing proof', user_id=current_user.id, details=f'Order ID: {order_id}, files: {saved}')
        notify_order_event(order_id, 'proof')
        flash(f'{saved} file(s) added. The customer has been asked to check them.', 'success')
    elif not skipped:
        flash('Choose at least one photo or video.', 'warning')
    return redirect(url_for('admin.admin_order_details', order_id=order_id))


@bp.route('/orders/<int:order_id>/proof/<int:proof_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_proof(order_id, proof_id):
    with get_db_connection() as conn:
        row = conn.execute('SELECT path FROM order_proofs WHERE id = ? AND order_id = ?', (proof_id, order_id)).fetchone()
        if row:
            conn.execute('DELETE FROM order_proofs WHERE id = ?', (proof_id,))
    if row:
        delete_proof_file(row['path'])
        log_activity('Admin deleted packing proof', user_id=current_user.id, details=f'Order ID: {order_id}')
        flash('File removed.', 'success')
    return redirect(url_for('admin.admin_order_details', order_id=order_id))


@bp.route('/orders/<int:order_id>/confirm-delivery', methods=['POST'])
@login_required
@admin_required
def admin_confirm_delivery(order_id):
    """Staff enter the PIN the customer shows at pickup (or tells them on the phone)."""
    ok, message = confirm_delivery_with_pin(order_id, request.form.get('pin'), f'staff ({current_user.username})')
    flash(message, 'success' if ok else 'danger')
    return redirect(url_for('admin.admin_order_details', order_id=order_id))
