"""Emails and SMS to customers."""
import os
import re
import threading
import requests
from flask import current_app, render_template
from flask_mail import Message
from shop.db import get_db_connection
from shop.extensions import mail, token_serializer
from shop.gifts import recipient_sms
from shop.helpers import SITE_NAME, get_shop_settings, public_url
from shop.security import log_activity


def mail_configured():
    return bool(current_app.config['MAIL_USERNAME'] and current_app.config['MAIL_PASSWORD'] and current_app.config['MAIL_DEFAULT_SENDER'])


def send_confirmation_email(user_email):
    token = token_serializer().dumps(user_email, salt='email-confirm')
    confirm_url = public_url('auth.confirm_email', token=token)
    subject = "Confirm Your Account"
    body = f"Thank you for registering! Please click the link to confirm your email address: {confirm_url}"
    msg = Message(subject, recipients=[user_email], body=body)
    try:
        mail.send(msg)
        log_activity("Confirmation email sent", details=f"To: {user_email}")
    except Exception as e:
        log_activity("Failed to send confirmation email", details=f"To: {user_email}, Error: {str(e)}")
        current_app.logger.error(f"Failed to send email: {e}") # Log to Flask logger


def send_verification_email(user_email, code):
    subject = "Your Verification Code"
    body = f"Your verification code is: {code}\n\nEnter this code on the verification page to activate your account."
    msg = Message(subject, recipients=[user_email], body=body)
    try:
        mail.send(msg)
        log_activity("Verification code email sent", details=f"To: {user_email}")
        return True
    except Exception as e:
        log_activity("Failed to send verification code", details=f"To: {user_email}, Error: {str(e)}")
        current_app.logger.error(f"Failed to send verification email: {e}")
        if current_app.debug:
            current_app.logger.warning(f"DEBUG MODE: verification code for {user_email} is {code}")
        return False


def send_account_exists_email(user_email):
    """Someone tried to register with an email that already has an account (we don't say so on the page)."""
    if not mail_configured():
        return False
    body = (f"Someone (hopefully you) tried to create a {SITE_NAME} account with this email address, "
            f"but you already have one.\n\nLog in here: {public_url('auth.login')}\n"
            f"Forgot your password? Reset it here: {public_url('auth.reset_request')}\n\n"
            "If this wasn't you, you can ignore this email - nothing has changed.")
    try:
        mail.send(Message(f"You already have a {SITE_NAME} account", recipients=[user_email], body=body))
        return True
    except Exception as e:
        current_app.logger.error(f"Account-exists email failed: {e}")
        return False


def _password_fingerprint(password_hash):
    # Part of the current hash goes into the reset token, so the link stops working once the password changes
    return (password_hash or '')[-16:]


def send_password_reset_email(user_email, password_hash=''):
    token = token_serializer().dumps([user_email, _password_fingerprint(password_hash)], salt='password-reset')
    reset_url = public_url('auth.reset_token', token=token)
    subject = "Password Reset Request"
    body = f"To reset your password, visit the following link: {reset_url}\nIf you did not make this request then simply ignore this email."
    msg = Message(subject, recipients=[user_email], body=body)
    try:
        mail.send(msg)
        log_activity("Password reset email sent", details=f"To: {user_email}")
    except Exception as e:
        log_activity("Failed to send password reset email", details=f"To: {user_email}, Error: {str(e)}")
        current_app.logger.error(f"Failed to send password reset email: {e}")


def sms_enabled():
    return bool(os.environ.get('AT_USERNAME') and os.environ.get('AT_API_KEY'))


def to_international(phone):
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('0'):
        digits = '254' + digits[1:]
    return '+' + digits if digits else ''


def send_sms(phone, text):
    """SMS via Africa's Talking (set AT_USERNAME and AT_API_KEY; username 'sandbox' uses their test system)."""
    if not sms_enabled() or not phone:
        return False
    username = os.environ['AT_USERNAME']
    host = 'api.sandbox.africastalking.com' if username == 'sandbox' else 'api.africastalking.com'
    data = {'username': username, 'to': to_international(phone), 'message': text[:459]}
    if os.environ.get('AT_SENDER_ID'):
        data['from'] = os.environ['AT_SENDER_ID']
    try:
        r = requests.post(f'https://{host}/version1/messaging', data=data, timeout=10,
                          headers={'apiKey': os.environ['AT_API_KEY'], 'Accept': 'application/json'})
        r.raise_for_status()
        return True
    except Exception as e:
        current_app.logger.error(f"SMS to {phone} failed: {e}")
        return False


STATUS_MESSAGES = {
    'Processing': ('Your order is confirmed', 'We have received your order and are preparing it.'),
    'Paid': ('Payment received', 'Thank you - your M-Pesa payment was received and we are preparing your order.'),
    'Shipped': ('Your order is on the way', 'Your order has left our shop and is on its way to you.'),
    'Delivered': ('Your order was delivered', 'Your order has been delivered. Enjoy, and thank you for shopping with us!'),
    'Cancelled': ('Your order was cancelled', 'Your order has been cancelled. If you paid already, we will refund you.'),
    'Refunded': ('Your refund is on its way', 'We have refunded your order.'),
    'Payment Failed': ('M-Pesa payment did not go through', 'The M-Pesa payment for your order was not completed, so the order was cancelled. You can order again any time.'),
}


PICKUP_OVERRIDES = {
    'Shipped': ('Your order is ready for pickup', 'Your order is ready - you can collect it now.'),
    'Delivered': ('Order collected', 'You have collected your order. Thank you for shopping with us!'),
}


def notify_order_event(order_id, event):
    """Email (and SMS, if enabled) the customer about their order. Runs in the background."""
    try:
        with get_db_connection() as conn:
            order = conn.execute('''SELECT o.*, u.email, u.username FROM orders o JOIN users u ON u.id = o.user_id
                                    WHERE o.id = ?''', (order_id,)).fetchone()
            if not order:
                return
            items = conn.execute('''SELECT oi.quantity, oi.price, p.name FROM order_items oi
                                    JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?''', (order_id,)).fetchall()
            last = conn.execute('SELECT note FROM order_status_history WHERE order_id = ? ORDER BY id DESC LIMIT 1',
                                (order_id,)).fetchone()
        order, items = dict(order), [dict(i) for i in items]
        note = last['note'] if (last and event == 'status') else None
        is_pickup = order.get('delivery_method') == 'pickup'
        is_gift = bool(order.get('is_gift'))
        receiver = (order.get('contact_name') or '').split(' ')[0]
        if event == 'deal_closed':
            headline = 'Your Pamoja deal has ended'
            message = (last['note'] if last else 'The deal has ended.') + f" Order total now: KSh {order['total_amount']:,.0f}."
        elif event == 'proof':
            headline = 'Check your item before we send it'
            message = ("We've added photos/video of the exact item we're packing for you. "
                       "Please take a look and tell us if it looks right.")
        elif event == 'placed':
            headline = f"Order #{order_id} received"
            message = ('Check your phone and enter your M-Pesa PIN to complete the payment.' if order['status'] == 'Pending Payment'
                       else ("We'll call you when it's ready to collect." if is_pickup else "We'll call you to arrange delivery."))
            if is_gift:
                message += f" We'll text {receiver} at each step, without prices."
        else:
            headline, message = (PICKUP_OVERRIDES.get(order['status']) if is_pickup else None) or STATUS_MESSAGES.get(order['status'], (None, None))
            if not headline:
                return   # statuses customers don't need to hear about
            if is_gift and order['status'] in ('Shipped', 'Delivered'):
                message = message.replace('your order', f"the order for {receiver}").replace('Your order', f"The order for {receiver}")
                message = message.replace('You have collected', f"{receiver} has collected").replace(' to you', f' to {receiver}')
            if order['status'] == 'Shipped' and order.get('delivery_pin') and is_gift:
                message += f" We've sent {receiver} the {'pickup' if is_pickup else 'delivery'} PIN by SMS (you can also see it on your order page)."
            elif order['status'] == 'Shipped' and order.get('delivery_pin'):
                message += (f" Your PIN is {order['delivery_pin']} - show it when you collect." if is_pickup else
                            f" Your delivery PIN is {order['delivery_pin']} - give it to the rider only when you have your parcel.")
        shop = get_shop_settings()
        order_url = public_url('orders.order_details', order_id=order_id)
        html = render_template('emails/order_update.html', order=order, items=items, headline=headline,
                               message=message, note=note, order_url=order_url, shop=shop, event=event)
        text = render_template('emails/order_update.txt', order=order, items=items, headline=headline,
                               message=message, note=note, order_url=order_url, shop=shop, event=event)
        sms_text = f"{SITE_NAME}: {headline} (#{order_id}). {message}" + (f" {note}" if note else '')
        buyer_phone = order.get('sender_phone') if is_gift else order.get('contact_phone')
        gift_text = recipient_sms(order, event)
        if gift_text and order['status'] in ('Processing', 'Paid') and not order.get('gift_notified_at'):
            with get_db_connection() as conn:   # the "parcel is coming" text goes out once
                conn.execute('UPDATE orders SET gift_notified_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
    except Exception as e:
        current_app.logger.error(f"Could not prepare notification for order {order_id}: {e}", exc_info=True)
        return

    app = current_app._get_current_object()   # the thread needs the real app, not the request proxy

    def deliver():
        with app.app_context():
            if mail_configured() and order.get('email'):
                try:
                    mail.send(Message(f"{headline} - {SITE_NAME}", recipients=[order['email']], body=text, html=html,
                                      sender=current_app.config['MAIL_DEFAULT_SENDER']))
                except Exception as e:
                    current_app.logger.error(f"Order email for #{order_id} failed: {e}")
            send_sms(buyer_phone, sms_text)
            if gift_text:
                send_sms(order.get('contact_phone'), gift_text)

    if current_app.config.get('TESTING'):
        deliver()   # tests run synchronously
    else:
        threading.Thread(target=deliver, daemon=True).start()
