"""Checkout, M-Pesa callback and the customer's orders."""
import ipaddress
import hmac
import re
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from shop.db import get_db_connection
from shop.extensions import csrf
from shop.forms import CheckoutForm
from shop.helpers import active_delivery_zones, delivery_fee_for, localtime_filter
from shop.notifications import notify_order_event
from shop.payments import ALLOWED_PAYMENT_METHODS, CheckoutError, lipa_na_mpesa_stk_push, mpesa_callback_signature, order_progress, record_order_status, release_order_stock
from shop.security import client_ip, log_activity


bp = Blueprint('orders', __name__)


@bp.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    form = CheckoutForm()
    with get_db_connection() as conn:
        cart_items_raw = conn.execute('''
            SELECT p.id as product_id, p.name, p.price, p.stock, c.quantity
            FROM carts c JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (current_user.id,)).fetchall()
        saved_phone = conn.execute('SELECT phone FROM users WHERE id = ?', (current_user.id,)).fetchone()['phone']

    if not cart_items_raw:
        flash('Your cart is empty. Add some products before checking out!', 'warning')
        return redirect(url_for('store.index'))

    # Validate stock for all cart items before proceeding
    for item in cart_items_raw:
        if item['quantity'] > item['stock']:
            flash(f"Not enough stock for {item['name']}. Only {item['stock']} available. Please update your cart.", 'danger')
            return redirect(url_for('cart.view_cart'))

    subtotal = sum(item['price'] * item['quantity'] for item in cart_items_raw)
    zones = active_delivery_zones()
    zones_by_id = {str(z['id']): z for z in zones}
    selected_zone = zones_by_id.get(form.delivery_zone.data or '')
    delivery_fee = delivery_fee_for(selected_zone, subtotal)
    total_amount = subtotal + delivery_fee

    if request.method == 'GET':
        # Pre-fill from the customer's account and last order
        form.contact_name.data = form.contact_name.data or current_user.username
        form.contact_phone.data = form.contact_phone.data or saved_phone

    def show_form():
        return render_template('checkout.html', form=form, cart_items=cart_items_raw, subtotal=subtotal,
                               delivery_fee=delivery_fee, total_amount=total_amount, zones=zones,
                               selected_zone=selected_zone, title='Checkout')

    if form.validate_on_submit():
        payment_method = form.payment_method.data
        shipping_address = (form.shipping_address.data or '').strip()
        if payment_method not in ALLOWED_PAYMENT_METHODS:
            flash('Please choose Cash on Delivery or Lipa na M-Pesa.', 'danger')
            return show_form()
        if not selected_zone:
            form.delivery_zone.errors.append('Please choose a delivery area or pickup point.')
            return show_form()
        is_pickup = selected_zone['kind'] == 'pickup'
        if not is_pickup and len(shipping_address) < 5:
            form.shipping_address.errors.append('Please enter your delivery address (street, building, town).')
            return show_form()
        if is_pickup:
            shipping_address = f"Pickup: {selected_zone['name']}"
        contact_phone = form.contact_phone.data
        # Begin transaction
        conn = get_db_connection()
        try:
            with conn: # Context manager handles commit or rollback
                # 1. Create Order
                order_cursor = conn.execute('''
                    INSERT INTO orders (user_id, subtotal, delivery_fee, total_amount, payment_method, shipping_address, status,
                                        contact_name, contact_phone, delivery_method, delivery_zone)
                    VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?)
                ''', (current_user.id, subtotal, delivery_fee, total_amount, payment_method, shipping_address,
                      form.contact_name.data.strip(), contact_phone, 'pickup' if is_pickup else 'delivery', selected_zone['name']))
                order_id = order_cursor.lastrowid

                # 2. Create Order Items and Update Product Stock
                for item in cart_items_raw:
                    conn.execute('''
                        INSERT INTO order_items (order_id, product_id, quantity, price)
                        VALUES (?, ?, ?, ?)
                    ''', (order_id, item['product_id'], item['quantity'], item['price']))
                    # Decrease stock in one step, only if enough is still left (two shoppers can't both buy the last item)
                    cur = conn.execute("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                                       (item['quantity'], item['product_id'], item['quantity']))
                    if cur.rowcount == 0:
                        raise CheckoutError(f"Sorry, {item['name']} just sold out or has fewer items left. Please update your cart.")

                # 3. Handle Payment
                transaction_id = None
                if payment_method == 'Lipa na M-Pesa':
                    mpesa_phone = form.phone_number.data or contact_phone
                    if not re.match(r'^(?:\+?254|0)[17]\d{8}$', mpesa_phone or ''):
                        raise CheckoutError('Enter the M-Pesa number to send the payment prompt to.')

                    mpesa_response = lipa_na_mpesa_stk_push(total_amount, mpesa_phone, order_id)
                    if mpesa_response.get('ResponseCode') == '0': # STK Push initiated successfully
                        transaction_id = mpesa_response.get('CheckoutRequestID')
                        conn.execute("UPDATE orders SET transaction_id = ?, status = 'Pending Payment' WHERE id = ?", (transaction_id, order_id))
                        record_order_status(conn, order_id, 'Pending Payment', 'M-Pesa payment prompt sent to your phone')
                    else:
                        error_desc = mpesa_response.get('ResponseDescription', 'M-Pesa payment initiation failed.')
                        raise CheckoutError(f'M-Pesa could not start the payment: {error_desc}') # Trigger rollback

                elif payment_method == 'Cash on Delivery':
                    conn.execute("UPDATE orders SET status = 'Processing' WHERE id = ?", (order_id,))
                    record_order_status(conn, order_id, 'Processing', 'Order received - pay when it arrives')

                # 4. Remember the phone for next time, clear the cart
                conn.execute('UPDATE users SET phone = ? WHERE id = ?', (contact_phone, current_user.id))
                conn.execute('DELETE FROM carts WHERE user_id = ?', (current_user.id,))
                # Commit is handled by 'with conn:' if no exceptions

            log_activity("Order placed", user_id=current_user.id, details=f"Order ID: {order_id}, Total: {total_amount}, Payment: {payment_method}")
            notify_order_event(order_id, 'placed')
            return redirect(url_for('orders.order_details', order_id=order_id, placed=1))

        except CheckoutError as ce: # Problem the customer can fix (stock, phone number, M-Pesa refused)
            flash(str(ce), 'danger')
            log_activity("Order placement failed", user_id=current_user.id, details=str(ce))
            return show_form()
        except Exception as e:
            # conn.rollback() is handled by 'with conn:'
            current_app.logger.error(f"Checkout failed: {e}", exc_info=True)
            flash('Sorry, something went wrong while placing your order. Please try again.', 'danger')
            log_activity("Order placement failed", user_id=current_user.id, details=str(e))
            return show_form()
        finally:
            conn.close() # Close connection explicitly if not using global app context connection management

    # GET request or form validation failed
    return show_form()


@bp.route('/mpesa_callback', methods=['POST'])
@csrf.exempt # M-Pesa callbacks won't have CSRF token
def mpesa_callback():
    # This is where Safaricom will send the STK Push result
    callback_data = request.get_json(silent=True) or {}
    order_id_ref = request.args.get('order_id', '') # Get order_id from query param we set in CallBackURL

    # Optional: only Safaricom's servers may call this (MPESA_CALLBACK_ALLOWED_IPS)
    allowed = current_app.config.get('MPESA_CALLBACK_ALLOWED_IPS')
    if allowed:
        try:
            ip = ipaddress.ip_address(client_ip())
            ok = any(ip in ipaddress.ip_network(net, strict=False) for net in allowed)
        except ValueError:
            ok = False
        if not ok:
            current_app.logger.warning(f'M-Pesa callback rejected: {client_ip()} is not an allowed IP')
            log_activity('M-Pesa callback rejected (IP not allowed)', details=f'IP: {client_ip()}')
            return jsonify({'ResultCode': 1, 'ResultDesc': 'Rejected'}), 403

    # Reject anything that doesn't carry the secret token we gave Safaricom for this order
    if not hmac.compare_digest(request.args.get('sig', ''), mpesa_callback_signature(order_id_ref)):
        current_app.logger.warning(f"M-Pesa callback rejected: bad signature for order {order_id_ref!r} from {request.remote_addr}")
        log_activity("M-Pesa callback rejected (bad signature)", details=f"Order ref: {order_id_ref}")
        return jsonify({"ResultCode": 1, "ResultDesc": "Rejected"}), 403

    current_app.logger.info(f"M-Pesa Callback Received for order {order_id_ref}: {callback_data}")

    try:
        result_code = callback_data['Body']['stkCallback']['ResultCode']
        checkout_request_id = callback_data['Body']['stkCallback']['CheckoutRequestID']

        with get_db_connection() as conn:
            order = conn.execute("SELECT * FROM orders WHERE transaction_id = ? AND id = ?",
                                 (checkout_request_id, order_id_ref)).fetchone()
            if not order:
                current_app.logger.error(f"M-Pesa Callback: Order not found for CheckoutRequestID {checkout_request_id} and Order Ref {order_id_ref}")
                return jsonify({"ResultCode": 1, "ResultDesc": "Order not found"}), 404 # Or 200 for M-Pesa

            if order['status'] != 'Pending Payment':
                # Already handled (Safaricom retries) or changed by an admin: don't overwrite it
                return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200

            if result_code == 0:
                # Payment successful: check the amount actually paid before marking the order paid
                items = callback_data['Body']['stkCallback'].get('CallbackMetadata', {}).get('Item', [])
                meta = {i.get('Name'): i.get('Value') for i in items}
                paid_amount = float(meta.get('Amount') or 0)
                if paid_amount + 0.5 < round(order['total_amount']):
                    current_app.logger.error(f"M-Pesa amount mismatch for order {order['id']}: paid {paid_amount}, expected {order['total_amount']}")
                    log_activity("M-Pesa amount mismatch", user_id=order['user_id'], details=f"Order ID: {order['id']}, Paid: {paid_amount}, Expected: {order['total_amount']}")
                    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200

                conn.execute("UPDATE orders SET status = 'Paid' WHERE id = ?", (order['id'],))
                record_order_status(conn, order['id'], 'Paid', f"M-Pesa receipt {meta.get('MpesaReceiptNumber') or ''}".strip())
                conn.commit()
                notify_order_event(order['id'], 'status')
                log_activity("M-Pesa payment successful", user_id=order['user_id'], details=f"Order ID: {order['id']}, Receipt: {meta.get('MpesaReceiptNumber')}, Amount: {paid_amount}")
                # TODO: Send order confirmation email to user
            else:
                # Payment failed or cancelled
                error_desc = callback_data['Body']['stkCallback'].get('ResultDesc', 'Payment failed.')
                conn.execute("UPDATE orders SET status = 'Payment Failed' WHERE id = ?", (order['id'],))
                release_order_stock(conn, order['id'])  # the items go back on sale
                record_order_status(conn, order['id'], 'Payment Failed', error_desc)
                conn.commit()
                notify_order_event(order['id'], 'status')
                log_activity("M-Pesa payment failed/cancelled", user_id=order['user_id'], details=f"Order ID: {order['id']}, Reason: {error_desc}")
                # TODO: Notify user of payment failure

        # Acknowledge receipt to M-Pesa
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200

    except Exception as e:
        current_app.logger.error(f"M-Pesa Callback Error: {str(e)} - Data: {callback_data}")
        log_activity("M-Pesa callback processing error", details=str(e))
        # Acknowledge receipt even on error to prevent M-Pesa retries if it's a processing fault on our side
        return jsonify({"ResultCode": 1, "ResultDesc": "Internal Server Error"}), 200 # Or 500 but M-Pesa might prefer 200


@bp.route('/orders')
@login_required
def order_history():
    with get_db_connection() as conn:
        orders_raw = conn.execute('''
            SELECT id, order_date, total_amount, status, payment_method
            FROM orders
            WHERE user_id = ?
            ORDER BY order_date DESC
        ''', (current_user.id,)).fetchall()

    # Convert to list of dicts and format date
    orders = []
    for row in orders_raw:
        order = dict(row)
        order['order_date_formatted'] = localtime_filter(order.get('order_date'), '%d %b %Y, %I:%M %p') or 'N/A'
        orders.append(order)

    return render_template('order_history.html', orders=orders, title='Your Orders') 


@bp.route('/orders/<int:order_id>') # This is order_details for the user
@login_required
def order_details(order_id): # Renamed from order_details_user to match your existing route
    with get_db_connection() as conn:
        # Ensure user can only see their own order
        order = conn.execute('''
            SELECT o.*, u.username
            FROM orders o JOIN users u ON o.user_id = u.id
            WHERE o.id = ? AND o.user_id = ?
        ''', (order_id, current_user.id)).fetchone()

        if not order:
            flash('Order not found or you do not have permission to view it.', 'danger')
            return redirect(url_for('orders.order_history'))

        order_items = conn.execute('''
            SELECT oi.quantity, oi.price, p.name, p.image, p.id as product_id
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (order_id,)).fetchall()
        history = conn.execute(
            'SELECT status, note, created_at FROM order_status_history WHERE order_id = ? ORDER BY created_at, id',
            (order_id,)).fetchall()

    order_dict = dict(order)
    order_dict['order_date_formatted'] = localtime_filter(order_dict.get('order_date')) or 'N/A'
    return render_template('order_details_user.html', order=order_dict, order_items=order_items,
                           history=history, just_placed=request.args.get('placed') == '1',
                           progress=order_progress(order_dict), title=f"Order #{order_id}")
