"""Shared helpers: money and dates, product photos, shop settings, delivery options, template values."""
import os
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import current_app, flash, request, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename
from shop.config import Config
from shop.db import DatabaseError, get_db_connection
from shop.security import valid_image_upload


SITE_NAME = Config.SITE_NAME
CURRENCY_SYMBOL = Config.CURRENCY_SYMBOL

PLACEHOLDER_IMAGE = 'images/placeholder.svg'


def product_img(path):
    """Static URL for a product/promo image, falling back to a placeholder when the file is missing."""
    if path and os.path.isfile(os.path.join(current_app.static_folder, path)):
        return url_for('static', filename=path)
    return url_for('static', filename=PLACEHOLDER_IMAGE)


SAFE_LINK_RE = re.compile(r'^(/(?!/)[^\s]*|https?://[^\s]+)$', re.I)


def safe_link_filter(url):
    """Only allow links to this site ('/...') or http(s) addresses; anything else (e.g. javascript:) becomes '#'."""
    url = (url or '').strip()
    return url if SAFE_LINK_RE.match(url) else '#'


SHOP_TZ = ZoneInfo(Config.SHOP_TIMEZONE)


def to_local(value):
    """Database times are UTC; show them in the shop's time zone (Nairobi by default)."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.strptime(value.split('.')[0], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHOP_TZ)


def localtime_filter(value, fmt='%d %b %Y, %H:%M'):
    local = to_local(value)
    return local.strftime(fmt) if local else (value or '')


def money_filter(value):
    """Format a number as currency, e.g. KSh 8,999 or KSh 1,249.50"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{CURRENCY_SYMBOL} 0"
    return f"{CURRENCY_SYMBOL} {v:,.0f}" if v == int(v) else f"{CURRENCY_SYMBOL} {v:,.2f}"


PRODUCT_CATEGORY_CHOICES = [
    ('Electronics', 'Electronics'),
    ('Home & Kitchen', 'Home & Kitchen'),
    ('Fashion & Wear', 'Fashion & Wear'),
    ('Beauty & Personal Care', 'Beauty & Personal Care'),
    ('Health & Wellness', 'Health & Wellness'),
    ('Sports & Outdoors', 'Sports & Outdoors'),
    ('Office & School', 'Office & School'),
]


def product_category_choices(extra=None):
    """Standard categories plus any category already used by a product, so editing an
    older product never silently switches it to the first option."""
    names = [c[0] for c in PRODUCT_CATEGORY_CHOICES]
    try:
        with get_db_connection() as conn:
            for row in conn.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category"):
                if row['category'] not in names:
                    names.append(row['category'])
    except DatabaseError as e:
        current_app.logger.error(f"Could not load product categories: {e}")
    if extra and extra not in names:
        names.append(extra)
    return [(n, n) for n in names]


def utility_processor():
    # Cart count logic
    cart_item_count = 0
    if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated: # Safety check
        try:
            with get_db_connection() as conn:
                result = conn.execute('SELECT SUM(quantity) as total FROM carts WHERE user_id = ?', (current_user.id,)).fetchone()
                if result and result['total'] is not None: # Ensure result and total are not None
                    cart_item_count = result['total']
        except Exception as e:
            current_app.logger.error(f"Error fetching cart count for context processor: {e}")
            pass # Default to 0 if error
    
    # Categories for navbar
    nav_categories = [choice[0] for choice in PRODUCT_CATEGORY_CHOICES]
    try:
        with get_db_connection() as conn:
            categories_data = conn.execute(
                "SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category LIMIT 7"
            ).fetchall()
            product_categories = [row['category'] for row in categories_data]
            if product_categories:
                nav_categories = product_categories
    except Exception as e:
         current_app.logger.error(f"Error fetching categories for navbar: {e}")


    # Products already in the user's wishlist (so cards can show a filled heart)
    wishlist_ids = set()
    if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
        try:
            with get_db_connection() as conn:
                wishlist_ids = {r['product_id'] for r in conn.execute(
                    'SELECT product_id FROM wishlists WHERE user_id = ?', (current_user.id,)).fetchall()}
        except Exception as e:
            current_app.logger.error(f"Error fetching wishlist ids: {e}")

    return dict(
        SHOP=get_shop_settings(),
        wishlist_ids=wishlist_ids,
        cart_item_count=cart_item_count,
        current_year=datetime.utcnow().year, # <<< THIS PROVIDES current_year
        SCRIPT_START_YEAR=2023, # Or your desired start year for copyright range
        categories_for_navbar=nav_categories
    )


MAX_PRODUCT_PHOTOS = 8


DEFAULT_PRODUCT_IMAGE = 'uploads/default.jpg'


def product_photos(conn, product_id, main_image):
    """All photos of a product: the main one first, then the extra ones."""
    photos = [main_image] if main_image and main_image != DEFAULT_PRODUCT_IMAGE else []
    photos += [r['path'] for r in conn.execute('SELECT path FROM product_images WHERE product_id = ? ORDER BY sort_order, id', (product_id,))]
    return photos


def _delete_upload(path):
    if path and path != DEFAULT_PRODUCT_IMAGE and path.startswith('uploads/'):
        full = os.path.join(current_app.static_folder, path)
        if os.path.isfile(full):
            try:
                os.remove(full)
            except OSError as e:
                current_app.logger.error(f"Could not delete {full}: {e}")


def save_product_photos(product_id):
    """Apply the photo section of the product form: remove ticked photos, add uploads, pick the main photo."""
    with get_db_connection() as conn:
        row = conn.execute('SELECT image FROM products WHERE id = ?', (product_id,)).fetchone()
        if not row:
            return
        photos = product_photos(conn, product_id, row['image'])
        # 1. remove (only photos that belong to this product)
        for path in request.form.getlist('remove_photo'):
            if path in photos:
                photos.remove(path)
                _delete_upload(path)
        # 2. add new uploads
        skipped = 0
        for f in request.files.getlist('photos'):
            if not f or not f.filename:
                continue
            if len(photos) >= MAX_PRODUCT_PHOTOS:
                skipped += 1
                continue
            if not valid_image_upload(f):
                flash(f'"{f.filename}" is not a PNG, JPG or GIF image and was skipped.', 'warning')
                continue
            filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(f.filename)}"
            f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            photos.append(f"uploads/{filename}")
        if skipped:
            flash(f'Only {MAX_PRODUCT_PHOTOS} photos per product - {skipped} photo(s) were not added.', 'warning')
        # 3. main photo
        wanted = request.form.get('main_photo')
        if wanted in photos:
            photos.remove(wanted)
            photos.insert(0, wanted)
        main = photos[0] if photos else DEFAULT_PRODUCT_IMAGE
        conn.execute('UPDATE products SET image = ? WHERE id = ?', (main, product_id))
        conn.execute('DELETE FROM product_images WHERE product_id = ?', (product_id,))
        conn.executemany('INSERT INTO product_images (product_id, path, sort_order) VALUES (?, ?, ?)',
                         [(product_id, p, i) for i, p in enumerate(photos[1:])])
        conn.commit()


SHOP_SETTING_FIELDS = [
    # key, label, help text
    ('tagline', 'Short description', 'One line about the shop, shown in the footer and link previews.'),
    ('phone', 'Phone number', 'Shown in the footer and on the Contact page, e.g. 0712 345 678.'),
    ('whatsapp', 'WhatsApp number', 'For the WhatsApp button, e.g. 0712 345 678 (leave empty to hide it).'),
    ('email', 'Email address', 'Customer support email.'),
    ('address', 'Shop address', 'Street, building and town.'),
    ('hours', 'Opening hours', 'e.g. Mon-Sat 8am-7pm.'),
    ('map_url', 'Google Maps link', 'Paste the "Share" link of your shop on Google Maps (optional).'),
    ('facebook', 'Facebook page link', 'Optional.'),
    ('instagram', 'Instagram link', 'Optional.'),
    ('tiktok', 'TikTok link', 'Optional.'),
    ('returns_days', 'Return window (days)', 'How many days customers have to return an item. Default 7.'),
]


SHOP_SETTING_DEFAULTS = {
    'tagline': 'Quality electronics, fashion and home essentials, delivered across Kenya.',
    'returns_days': '7',
}


def get_shop_settings():
    values = dict(SHOP_SETTING_DEFAULTS)
    try:
        with get_db_connection() as conn:
            for row in conn.execute('SELECT key, value FROM settings'):
                if row['value'] not in (None, ''):
                    values[row['key']] = row['value']
    except DatabaseError as e:
        current_app.logger.error(f"Could not load shop settings: {e}")
    wa = re.sub(r'\D', '', values.get('whatsapp', ''))
    if wa.startswith('0'):
        wa = '254' + wa[1:]
    values['whatsapp_link'] = f'https://wa.me/{wa}' if wa else ''
    return values


def active_delivery_zones():
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT * FROM delivery_zones WHERE is_active = 1 ORDER BY sort_order, id').fetchall()]


def delivery_fee_for(zone, subtotal):
    """Fee for a delivery option, honouring its free-delivery threshold."""
    if not zone:
        return 0
    if zone.get('free_over') and subtotal >= zone['free_over']:
        return 0
    return float(zone.get('fee') or 0)
