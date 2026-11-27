"""Orders and payments: M-Pesa, stock, order status."""
import secrets
import base64
import hashlib
import hmac
from datetime import datetime
import requests
from flask import current_app


ALLOWED_PAYMENT_METHODS = ('Cash on Delivery', 'Lipa na M-Pesa')


STOCK_RELEASED_STATUSES = ('Cancelled', 'Payment Failed')


class CheckoutError(Exception):
    """A checkout problem with a message that is safe to show the customer."""


def mpesa_callback_signature(order_id):
    """Secret per-order token added to the M-Pesa callback URL, so only Safaricom (who received
    the URL from us) can report a payment for this order."""
    return hmac.new(current_app.config['SECRET_KEY'].encode(), f"mpesa-callback:{order_id}".encode(), hashlib.sha256).hexdigest()


ORDER_STEPS_DELIVERY = ['Order placed', 'Confirmed', 'On the way', 'Delivered']


ORDER_STEPS_PICKUP = ['Order placed', 'Confirmed', 'Ready for pickup', 'Collected']


STATUS_STEP = {'Pending': 0, 'Pending Payment': 0, 'Processing': 1, 'Paid': 1, 'Shipped': 2, 'Delivered': 3}


def order_progress(order):
    """Steps for the order tracker, and how far along the order is (None if cancelled/failed)."""
    steps = ORDER_STEPS_PICKUP if order.get('delivery_method') == 'pickup' else ORDER_STEPS_DELIVERY
    return {'steps': steps, 'current': STATUS_STEP.get(order.get('status'))}


def record_order_status(conn, order_id, status, note=None):
    """Add a step to the order's timeline (shown on the customer's order page)."""
    conn.execute('INSERT INTO order_status_history (order_id, status, note) VALUES (?, ?, ?)', (order_id, status, note))


def release_order_stock(conn, order_id):
    """Put an order's items back into stock (order cancelled or payment failed)."""
    for item in conn.execute('SELECT product_id, quantity FROM order_items WHERE order_id = ?', (order_id,)).fetchall():
        conn.execute('UPDATE products SET stock = stock + ? WHERE id = ?', (item['quantity'], item['product_id']))


def reserve_order_stock(conn, order_id):
    """Take an order's items out of stock again (e.g. a cancelled order is reopened).
    Raises CheckoutError if there isn't enough stock left."""
    items = conn.execute('''SELECT oi.product_id, oi.quantity, p.name FROM order_items oi
                            JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?''', (order_id,)).fetchall()
    for item in items:
        cur = conn.execute('UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?',
                           (item['quantity'], item['product_id'], item['quantity']))
        if cur.rowcount == 0:
            raise CheckoutError(f"Not enough stock of {item['name']} to reopen this order.")


def lipa_na_mpesa_stk_push(amount, phone_number, order_id_ref):
    """Initiates an STK Push request to M-Pesa."""
    try:
        amount = int(round(amount)) # M-Pesa requires an integer amount
        phone_number = str(phone_number)
        if phone_number.startswith('0'):
            phone_number = '254' + phone_number[1:]
        elif phone_number.startswith('+'):
            phone_number = phone_number[1:]

        # 1. Get Access Token
        auth_url = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
        auth_response = requests.get(auth_url, auth=(current_app.config['MPESA_CONSUMER_KEY'], current_app.config['MPESA_CONSUMER_SECRET']))
        auth_response.raise_for_status() # Raise HTTPError for bad responses (4XX or 5XX)
        access_token = auth_response.json()['access_token']

        # 2. Prepare STK Push Request
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_string = current_app.config['MPESA_BUSINESS_SHORT_CODE'] + current_app.config['MPESA_PASSKEY'] + timestamp
        password = base64.b64encode(password_string.encode()).decode()

        stk_push_url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        headers = {'Authorization': f'Bearer {access_token}'}
        payload = {
            "BusinessShortCode": current_app.config['MPESA_BUSINESS_SHORT_CODE'],
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline", # or "CustomerBuyGoodsOnline" depending on your shortcode type
            "Amount": amount,
            "PartyA": phone_number, # Customer's phone number
            "PartyB": current_app.config['MPESA_BUSINESS_SHORT_CODE'], # Your organization's shortcode
            "PhoneNumber": phone_number, # Customer's phone number again
            "CallBackURL": current_app.config['MPESA_CALLBACK_URL'] + f"?order_id={order_id_ref}&sig={mpesa_callback_signature(order_id_ref)}", # order id + secret token
            "AccountReference": f"ORDER-{order_id_ref}", # Custom reference for the transaction
            "TransactionDesc": f"Payment for Order #{order_id_ref}"
        }

        response = requests.post(stk_push_url, json=payload, headers=headers)
        response.raise_for_status()
        # (No log_activity here: checkout calls this while its database transaction is open)
        current_app.logger.info(f"M-Pesa STK Push initiated for order {order_id_ref}: {response.json()}")
        return response.json() # Contains CheckoutRequestID, ResponseCode, etc.

    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"M-Pesa RequestException: {e} - Response: {e.response.text if e.response else 'No response'}")
        return {"ResponseCode": "1", "ResponseDescription": "M-Pesa is not reachable right now. Please try again or choose Cash on Delivery."}
    except Exception as e:
        current_app.logger.error(f"M-Pesa general error: {e}")
        return {"ResponseCode": "1", "ResponseDescription": "M-Pesa is not reachable right now. Please try again or choose Cash on Delivery."}


PIN_MAX_ATTEMPTS = 5   # per order, per 15 minutes


def confirm_delivery_with_pin(order_id, pin, who):
    """Mark an order Delivered if `pin` is its delivery PIN. Returns (ok, message)."""
    from shop.db import get_db_connection
    from shop.notifications import notify_order_event
    from shop.security import log_activity, rate_limited, record_rate_event
    key = f'order:{order_id}'
    if rate_limited('pin_fail', key, PIN_MAX_ATTEMPTS, 15):
        return False, 'Too many wrong PINs. Wait 15 minutes, or call the customer.'
    pin = (pin or '').strip()
    with get_db_connection() as conn:
        order = conn.execute('SELECT status, delivery_pin FROM orders WHERE id = ?', (order_id,)).fetchone()
        if not order:
            return False, 'Order not found.'
        if order['status'] == 'Delivered':
            return True, 'This order is already marked delivered.'
        if order['status'] != 'Shipped' or not order['delivery_pin']:
            return False, 'This order is not out for delivery yet.'
        if not secrets.compare_digest(pin, order['delivery_pin']):
            record_rate_event('pin_fail', key)
            log_activity('Wrong delivery PIN', details=f'Order ID: {order_id}, by {who}')
            return False, "That PIN is not right. Ask the customer to check the SMS or their order page."
        # Only one confirmation wins, even if two people press at the same moment
        cur = conn.execute("UPDATE orders SET status = 'Delivered', delivered_with_pin = 1 WHERE id = ? AND status = 'Shipped'", (order_id,))
        if cur.rowcount == 0:
            return True, 'This order is already marked delivered.'
        record_order_status(conn, order_id, 'Delivered', f"Confirmed with the customer's PIN ({who})")
    log_activity('Delivery confirmed with PIN', details=f'Order ID: {order_id}, by {who}')
    notify_order_event(order_id, 'status')
    return True, 'Delivered - confirmed with the customer\'s PIN. Thank you!'
