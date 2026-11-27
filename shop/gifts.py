"""Tuma kwa Mama: orders sent to someone else (family, friends).

For a gift order the order's contact_name/contact_phone are the RECIPIENT (the rider calls them and
they get the delivery PIN); sender_name/sender_phone are the buyer. The recipient gets SMS updates
without prices (except the amount to pay, for cash on delivery) and a tracking link that needs no account.
"""
from itsdangerous import BadSignature, SignatureExpired
from flask import url_for

from shop.extensions import token_serializer
from shop.helpers import SITE_NAME

GIFT_LINK_DAYS = 60
GIFT_MESSAGE_MAX = 160


def gift_token(order_id):
    return token_serializer().dumps({'o': order_id}, salt='gift-track')


def order_from_gift_token(token):
    try:
        data = token_serializer().loads(token, salt='gift-track', max_age=GIFT_LINK_DAYS * 86400)
        return int(data['o'])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def first_name(name):
    return (name or '').strip().split(' ')[0] or 'Someone'


def _ksh(amount):
    amount = float(amount or 0)
    return f"KSh {amount:,.0f}" if amount == int(amount) else f"KSh {amount:,.2f}"


def recipient_sms(order, event):
    """SMS for the gift's recipient, or None if they don't need one for this event.

    `order` is a dict of the orders row. `event` is 'placed', 'status' or 'resend'.
    """
    if not order.get('is_gift'):
        return None
    status = order.get('status')
    sender = first_name(order.get('sender_name'))
    pickup = order.get('delivery_method') == 'pickup'
    pin = order.get('delivery_pin')
    oid = order['id']

    if status == 'Shipped' and pin and event in ('status', 'resend'):
        if pickup:
            return (f"{SITE_NAME}: Your parcel from {sender} is ready to collect at {order.get('delivery_zone')}. "
                    f"Show pickup code {pin} at the counter. (Order #{oid})")
        return (f"{SITE_NAME}: Your parcel from {sender} is on the way. Give the rider PIN {pin} only after "
                f"you have the parcel. (Order #{oid})")
    if status == 'Delivered' and event == 'status':
        return f"{SITE_NAME}: Your parcel from {sender} has been {'collected' if pickup else 'delivered'}. Enjoy!"
    if status in ('Cancelled', 'Payment Failed') and event == 'status' and order.get('gift_notified_at'):
        return f"{SITE_NAME}: Sorry, the parcel {sender} sent you (order #{oid}) was cancelled. {sender} has been told."
    # "a parcel is coming": once the order is confirmed (cash on delivery, or M-Pesa paid)
    if status in ('Processing', 'Paid') and (event == 'resend' or not order.get('gift_notified_at')):
        text = f"{SITE_NAME}: {sender} has sent you a parcel! (Order #{oid})"
        if order.get('gift_message'):
            text += f' "{order["gift_message"]}"'
        text += (f" Collect it at {order.get('delivery_zone')} - we'll SMS you a code when it's ready."
                 if pickup else " We'll call you to deliver it.")
        if order.get('payment_method') == 'Cash on Delivery':
            text += f" Please have {_ksh(order.get('total_amount'))} ready to pay {'at pickup' if pickup else 'on delivery'}."
        text += f" Track: {url_for('orders.gift_tracking', token=gift_token(oid), _external=True)}"
        return text
    return None
