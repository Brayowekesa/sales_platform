"""Checkout, M-Pesa callback and the customer's orders."""
from shop.delivery import order_from_rider_token, proof_file_path
import ipaddress
import hmac
import re
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from shop.db import get_db_connection
from shop.extensions import csrf
from shop.forms import CheckoutForm
from shop.helpers import active_delivery_zones, delivery_fee_for, localtime_filter
from shop.gifts import order_from_gift_token, recipient_sms
from shop.notifications import notify_order_event, send_sms, sms_enabled, to_international
from shop.payments import ALLOWED_PAYMENT_METHODS, CheckoutError, confirm_delivery_with_pin, lipa_na_mpesa_stk_push, mpesa_callback_signature, order_progress, record_order_status, release_order_stock
from shop.security import client_ip, log_activity, rate_limited, record_rate_event


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
        landmark = (form.landmark.data or '').strip() or None
        lat = lng = None
        if form.location.data and not is_pickup:
            try:
                lat, lng = (float(v) for v in form.location.data.split(','))
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    lat = lng = None
            except ValueError:
                lat = lng = None
        if is_pickup:
            landmark = None
            shipping_address = f"Pickup: {selected_zone['name']}"
        contact_phone = form.contact_phone.data
        # Tuma kwa Mama: the recipient becomes the order's contact (rider calls them, they get the PIN)
        is_gift = bool(form.is_gift.data)
        receiver_name, receiver_phone = form.contact_name.data.strip(), contact_phone
        sender_name = sender_phone = gift_message = None
        if is_gift:
            receiver_name = (form.recipient_name.data or '').strip()
            receiver_phone = form.recipient_phone.data or ''
            if not receiver_name:
                form.recipient_name.errors.append("Please enter the name of the person you're sending to.")
            if not receiver_phone:
                form.recipient_phone.errors.append('We need their phone number to deliver and send them updates.')
            elif to_international(receiver_phone) == to_international(contact_phone):
                form.recipient_phone.errors.append("That's your own number - enter the recipient's phone.")
            if form.recipient_name.errors or form.recipient_phone.errors:
                return show_form()
            sender_name, sender_phone = form.contact_name.data.strip(), contact_phone
            gift_message = (form.gift_message.data or '').strip() or None
        # Begin transaction
        conn = get_db_connection()
        try:
            with conn: # Context manager handles commit or rollback
                # 1. Create Order
                order_cursor = conn.execute('''
                    INSERT INTO orders (user_id, subtotal, delivery_fee, total_amount, payment_method, shipping_address, status,
                                        contact_name, contact_phone, delivery_method, delivery_zone, delivery_landmark, delivery_lat, delivery_lng,
                                        is_gift, sender_name, sender_phone, gift_message)
                    VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (current_user.id, subtotal, delivery_fee, total_amount, payment_method, shipping_address,
                      receiver_name, receiver_phone, 'pickup' if is_pickup else 'delivery', selected_zone['name'],
                      landmark, lat, lng, 1 if is_gift else 0, sender_name, sender_phone, gift_message))
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
        proofs = conn.execute('SELECT id, kind FROM order_proofs WHERE order_id = ? ORDER BY id', (order_id,)).fetchall()

    order_dict = dict(order)
    gift_link = None
    if order_dict.get('is_gift'):
        from shop.gifts import gift_token
        gift_link = url_for('orders.gift_tracking', token=gift_token(order_id), _external=True)
    order_dict['order_date_formatted'] = localtime_filter(order_dict.get('order_date')) or 'N/A'
    return render_template('order_details_user.html', order=order_dict, order_items=order_items,
                           history=history, proofs=proofs, just_placed=request.args.get('placed') == '1',
                           gift_link=gift_link, sms_on=sms_enabled(),
                           progress=order_progress(order_dict), title=f"Order #{order_id}")


# --- Packing proof (customer side) and the rider's delivery page ---
@bp.route('/orders/<int:order_id>/proof/<int:proof_id>')
@login_required
def proof_file(order_id, proof_id):
    """Packing-proof photos/videos are private: only the order's customer and admins can see them."""
    with get_db_connection() as conn:
        row = conn.execute('''SELECT p.path, o.user_id FROM order_proofs p JOIN orders o ON o.id = p.order_id
                              WHERE p.id = ? AND p.order_id = ?''', (proof_id, order_id)).fetchone()
    if not row or (row['user_id'] != current_user.id and not current_user.is_admin):
        abort(404)
    path = proof_file_path(row['path'])
    if not path:
        abort(404)
    response = send_file(path, conditional=True, max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@bp.route('/orders/<int:order_id>/proof-response', methods=['POST'])
@login_required
def proof_response(order_id):
    answer = request.form.get('answer')
    note = (request.form.get('note') or '').strip()[:300]
    if answer not in ('ok', 'problem') or (answer == 'problem' and not note):
        flash("Please tell us what's wrong so we can fix it before sending.", 'warning')
        return redirect(url_for('orders.order_details', order_id=order_id))
    with get_db_connection() as conn:
        order = conn.execute('SELECT id FROM orders WHERE id = ? AND user_id = ?', (order_id, current_user.id)).fetchone()
        if not order:
            abort(404)
        conn.execute('UPDATE orders SET proof_response = ?, proof_response_note = ?, proof_response_at = CURRENT_TIMESTAMP WHERE id = ?',
                     (answer, note or None, order_id))
        record_order_status(conn, order_id, 'Customer checked the item',
                            'Looks right' if answer == 'ok' else f'Reported a problem: {note}')
    log_activity('Customer answered packing proof', user_id=current_user.id, details=f'Order ID: {order_id}, answer: {answer}')
    if answer == 'ok':
        flash("Thanks for checking! We'll send it out.", 'success')
    else:
        flash("Thanks for telling us - we'll contact you before sending anything.", 'info')
    return redirect(url_for('orders.order_details', order_id=order_id))


@bp.route('/orders/<int:order_id>/gift-resend', methods=['POST'])
@login_required
def gift_resend(order_id):
    """Buyer re-sends the latest SMS to the person they're sending to (e.g. they lost the PIN)."""
    with get_db_connection() as conn:
        order = conn.execute('SELECT * FROM orders WHERE id = ? AND user_id = ? AND is_gift = 1', (order_id, current_user.id)).fetchone()
    if not order:
        abort(404)
    text = recipient_sms(dict(order), 'resend')
    if not text:
        flash('There is nothing to send right now - we text them automatically at each step.', 'info')
    elif not sms_enabled():
        flash("Text messages aren't switched on for this shop yet - use the WhatsApp button to send them the tracking link.", 'warning')
    elif rate_limited('gift_resend', str(order_id), 3, 60):
        flash("We've already re-sent it a few times - please try again in an hour.", 'warning')
    else:
        record_rate_event('gift_resend', str(order_id))
        send_sms(order['contact_phone'], text)
        log_activity('Gift SMS re-sent', user_id=current_user.id, details=f'Order ID: {order_id}')
        flash(f"Sent again to {order['contact_name']}.", 'success')
    return redirect(url_for('orders.order_details', order_id=order_id))


@bp.route('/gift/<token>')
def gift_tracking(token):
    """Tracking page for the person receiving a gift (no account, no prices)."""
    order_id = order_from_gift_token(token)
    if not order_id:
        return render_template('gift_tracking.html', expired=True, title='Link expired'), 410
    with get_db_connection() as conn:
        order = conn.execute('''SELECT id, status, contact_name, sender_name, gift_message, delivery_method, delivery_zone,
                                       payment_method, total_amount, is_gift, order_date
                                FROM orders WHERE id = ? AND is_gift = 1''', (order_id,)).fetchone()
        if not order:
            abort(404)
        items = conn.execute('''SELECT oi.quantity, p.name, p.image FROM order_items oi JOIN products p ON p.id = oi.product_id
                                WHERE oi.order_id = ?''', (order_id,)).fetchall()
    order = dict(order)
    response = current_app.make_response(render_template('gift_tracking.html', order=order, items=items,
                                                         progress=order_progress(order), title='Your parcel'))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Robots-Tag'] = 'noindex'
    return response


@bp.route('/deliver/<token>', methods=['GET', 'POST'])
def rider_delivery(token):
    """Page for the rider (no account needed): shows where to deliver and takes the customer's PIN."""
    order_id = order_from_rider_token(token)
    if not order_id:
        return render_template('rider.html', expired=True, title='Delivery link expired'), 410
    result = None
    if request.method == 'POST':
        result = confirm_delivery_with_pin(order_id, request.form.get('pin'), 'rider')
    with get_db_connection() as conn:
        order = conn.execute('''SELECT id, status, contact_name, contact_phone, delivery_method, delivery_zone, shipping_address,
                                       delivery_landmark, delivery_lat, delivery_lng, is_gift, sender_name
                                FROM orders WHERE id = ?''', (order_id,)).fetchone()
        items = conn.execute('''SELECT oi.quantity, p.name FROM order_items oi JOIN products p ON p.id = oi.product_id
                                WHERE oi.order_id = ?''', (order_id,)).fetchall()
    if not order:
        abort(404)
    response = current_app.make_response(render_template('rider.html', order=order, items=items, result=result,
                                                         token=token, title=f'Deliver order #{order_id}'))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Robots-Tag'] = 'noindex'
    return response
