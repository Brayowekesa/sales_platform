"""Cart and wishlist."""
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from shop.db import IntegrityError, get_db_connection
from shop.forms import UpdateCartForm
from shop.security import log_activity, same_site_referrer


bp = Blueprint('cart', __name__)


@bp.route('/cart')
@login_required
def view_cart():
    update_forms = {} # To hold a form for each item
    with get_db_connection() as conn:
        cart_items = conn.execute('''
            SELECT p.id, p.name, p.price, p.image, p.stock, c.quantity
            FROM carts c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (current_user.id,)).fetchall()

        # Prepare update forms for each cart item
        for item in cart_items:
            update_forms[item['id']] = UpdateCartForm(quantity=item['quantity'])


    total_price = sum(item['price'] * item['quantity'] for item in cart_items)
    return render_template('cart.html', cart_items=cart_items, total_price=total_price, update_forms=update_forms, title='Your Cart')


@bp.route('/cart/add/<int:product_id>', methods=['POST'])
@login_required
def add_to_cart(product_id):
    try:
        quantity = int(request.form.get('quantity', 1))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Quantity must be a valid number.'}), 400

    if quantity <= 0:
        return jsonify({'success': False, 'message': 'Quantity must be positive.'}), 400

    try:
        with get_db_connection() as conn:
            product = conn.execute("SELECT id, name, stock FROM products WHERE id = ?", (product_id,)).fetchone()
            if not product:
                return jsonify({'success': False, 'message': 'Product not found.'}), 404

            current_in_cart = conn.execute(
                'SELECT quantity FROM carts WHERE user_id = ? AND product_id = ?',
                (current_user.id, product_id)
            ).fetchone()
            current_in_cart_quantity = current_in_cart['quantity'] if current_in_cart else 0
            remaining_stock = max(0, product['stock'] - current_in_cart_quantity)

            if product['stock'] <= 0:
                return jsonify({'success': False, 'message': 'This product is currently out of stock.'}), 400
            if quantity > remaining_stock:
                return jsonify({'success': False, 'message': f"Only {remaining_stock} left in stock for {product['name']}."}), 400

            conn.execute('''
                INSERT INTO carts (user_id, product_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, product_id)
                DO UPDATE SET quantity = CASE
                    WHEN carts.quantity + excluded.quantity > (SELECT stock FROM products WHERE id = excluded.product_id)
                    THEN (SELECT stock FROM products WHERE id = excluded.product_id)
                    ELSE carts.quantity + excluded.quantity END
            ''', (current_user.id, product_id, quantity))
            conn.commit()

            new_count_result = conn.execute(
                'SELECT SUM(quantity) as total FROM carts WHERE user_id = ?', (current_user.id,)
            ).fetchone()
            new_count = new_count_result['total'] if new_count_result and new_count_result['total'] else 0

        if request.form.get('from_wishlist'):
            # "Move to cart" on the wishlist page: the item leaves the wishlist
            with get_db_connection() as conn:
                conn.execute('DELETE FROM wishlists WHERE user_id = ? AND product_id = ?', (current_user.id, product_id))
                conn.commit()
        log_activity("Added to cart", user_id=current_user.id, details=f"Product ID: {product_id}, Quantity: {quantity}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': 'Item added to cart!', 'count': new_count})
        flash('Item added to cart!', 'success')
        return redirect(same_site_referrer() or url_for('store.index'))

    except IntegrityError:
        return jsonify({'success': False, 'message': 'Error adding item. Product may not exist or conflict.'}), 400
    except Exception as e:
        log_activity("Add to cart failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
        current_app.logger.error(f"Error adding to cart: {e}")
        return jsonify({'success': False, 'message': 'Sorry, something went wrong. Please try again.'}), 500


@bp.route('/cart/remove/<int:product_id>', methods=['POST'])
@login_required
def remove_from_cart(product_id):
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM carts WHERE user_id = ? AND product_id = ?', (current_user.id, product_id))
            conn.commit()
        flash('Item removed from cart.', 'success')
        log_activity("Removed from cart", user_id=current_user.id, details=f"Product ID: {product_id}")
    except Exception as e:
        flash('Sorry, that item could not be removed. Please try again.', 'danger')
        log_activity("Remove from cart failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(url_for('cart.view_cart'))


@bp.route('/cart/update/<int:product_id>', methods=['POST'])
@login_required
def update_cart_item(product_id):
    form = UpdateCartForm() # Use this for CSRF protection if nothing else
    if form.validate_on_submit(): # Basic validation, or just check CSRF if only CSRF is needed
        try:
            new_quantity = int(request.form.get('quantity')) # Get quantity from the specific form for this item
            if new_quantity <= 0:
                # If quantity is 0 or less, remove the item
                return remove_from_cart(product_id)

            with get_db_connection() as conn:
                product = conn.execute("SELECT id, name, stock FROM products WHERE id = ?", (product_id,)).fetchone()
                if not product:
                    flash('Product not found in cart.', 'danger')
                    return redirect(url_for('cart.view_cart'))
                if product['stock'] <= 0:
                    flash(f"{product['name']} is currently out of stock. Cart not updated.", 'warning')
                    return redirect(url_for('cart.view_cart'))
                if product['stock'] < new_quantity:
                    flash(f"Not enough stock for {product['name']}. Only {product['stock']} available. Cart not updated.", 'warning')
                    return redirect(url_for('cart.view_cart'))

                conn.execute('''
                    UPDATE carts SET quantity = ?
                    WHERE user_id = ? AND product_id = ?
                ''', (new_quantity, current_user.id, product_id))
                conn.commit()
            flash('Cart updated.', 'success')
            log_activity("Cart updated", user_id=current_user.id, details=f"Product ID: {product_id}, New Quantity: {new_quantity}")
        except ValueError:
            flash('Invalid quantity.', 'danger')
        except Exception as e:
            flash('Sorry, your cart could not be updated. Please try again.', 'danger')
            log_activity("Cart update failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    else:
        flash('Invalid form submission for cart update.', 'danger')
    return redirect(url_for('cart.view_cart'))


@bp.route('/cart/empty', methods=['POST'])
@login_required
def empty_cart():
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM carts WHERE user_id = ?', (current_user.id,))
            conn.commit()
        flash('Cart emptied successfully.', 'success')
        log_activity("Cart emptied", user_id=current_user.id)
    except Exception as e:
        flash('Sorry, your cart could not be emptied. Please try again.', 'danger')
        log_activity("Cart empty failed", user_id=current_user.id, details=str(e))
    return redirect(url_for('cart.view_cart'))


@bp.route('/cart/count', methods=['GET']) # Primarily for AJAX
@login_required
def cart_count_api(): # Renamed from original to avoid conflict if you had a template variable named cart_count
    with get_db_connection() as conn:
        result = conn.execute('SELECT SUM(quantity) as total FROM carts WHERE user_id = ?', (current_user.id,)).fetchone()
    count = result['total'] if result and result['total'] else 0
    return jsonify({'count': count})


@bp.route('/wishlist')
@login_required
def view_wishlist():
    with get_db_connection() as conn:
        wishlist_items = conn.execute('''
            SELECT p.id, p.name, p.price, p.original_price, p.category, p.image, p.stock
            FROM wishlists w
            JOIN products p ON w.product_id = p.id
            WHERE w.user_id = ?
        ''', (current_user.id,)).fetchall()
    return render_template('wishlist.html', wishlist_items=wishlist_items, title='Your Wishlist')


@bp.route('/wishlist/add/<int:product_id>', methods=['POST'])
@login_required
def add_to_wishlist(product_id):
    try:
        with get_db_connection() as conn:
            # Check if product exists
            product = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
            if not product:
                flash('Product not found.', 'danger')
                return redirect(same_site_referrer() or url_for('store.index'))

            conn.execute('INSERT INTO wishlists (user_id, product_id) VALUES (?, ?)', (current_user.id, product_id))
            conn.commit()
        flash('Item added to wishlist!', 'success')
        log_activity("Added to wishlist", user_id=current_user.id, details=f"Product ID: {product_id}")
    except IntegrityError:
        flash('Item is already in your wishlist.', 'info')
    except Exception as e:
        flash('Sorry, that item could not be added to your wishlist.', 'danger')
        log_activity("Add to wishlist failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(same_site_referrer() or url_for('cart.view_wishlist'))


@bp.route('/wishlist/remove/<int:product_id>', methods=['POST'])
@login_required
def remove_from_wishlist(product_id):
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM wishlists WHERE user_id = ? AND product_id = ?', (current_user.id, product_id))
            conn.commit()
        flash('Item removed from wishlist.', 'success')
        log_activity("Removed from wishlist", user_id=current_user.id, details=f"Product ID: {product_id}")
    except Exception as e:
        flash('Sorry, that item could not be removed from your wishlist.', 'danger')
        log_activity("Remove from wishlist failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(same_site_referrer() or url_for('cart.view_wishlist'))
