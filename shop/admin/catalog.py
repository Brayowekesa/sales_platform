"""Admin: products (with photos) and promotions."""
from shop.pricing import apply_price_change, send_wishlist_alerts
import os
import uuid
from datetime import datetime
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
from shop.db import DatabaseError, IntegrityError, get_db_connection
from shop.forms import ProductForm, PromotionForm
from shop.helpers import (MAX_PRODUCT_PHOTOS, _delete_upload, max_upload_mb, product_category_choices, product_photos,
                          save_product_photos, valid_photo_upload)
from shop.security import admin_required, log_activity
from shop.images import save_clean_image

from shop.admin import bp


@bp.route('/products')
@login_required
@admin_required
def admin_products():
    page = max(1, min(request.args.get('page', 1, type=int), 10000))   # a huge or negative page can't break the query
    per_page = 10  # Number of products per admin page

    with get_db_connection() as conn:
        total_products_data = conn.execute('SELECT COUNT(id) FROM products').fetchone()
        total_products_count = total_products_data[0] if total_products_data else 0

        offset = (page - 1) * per_page
        raw_products = conn.execute(
            'SELECT id, name, category, price, stock, added_at, image FROM products ORDER BY added_at DESC LIMIT ? OFFSET ?',
            (per_page, offset)
        ).fetchall()

        products = []
        for row in raw_products:
            product_dict = dict(row) # Convert the row to a mutable dictionary
            if product_dict.get('added_at') and isinstance(product_dict['added_at'], str):
                try:
                    # Attempt to parse the datetime string. Adjust format if necessary.
                    # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                    # If it includes milliseconds: '%Y-%m-%d %H:%M:%S.%f'
                    product_dict['added_at'] = datetime.strptime(product_dict['added_at'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    # Handle cases where parsing might fail, e.g., log it or keep as string
                    current_app.logger.warning(f"Could not parse added_at string: {product_dict['added_at']} for product ID: {product_dict['id']}")
                    # Optionally, set to None or keep as string, so template logic `if product.added_at` still works
                    # product_dict['added_at'] = None # Or keep as string and handle in template
            products.append(product_dict)

        total_pages = (total_products_count + per_page - 1) // per_page if total_products_count > 0 else 0
        pagination_query_params = {}  # Add any filter params here if you implement them

    return render_template(
        'admin/products.html',
        products=products, # Pass the processed list
        title='Manage Products',
        current_page=page,
        total_pages=total_pages,
        endpoint='admin.admin_products',
        query_params=pagination_query_params
    )


@bp.route('/products/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_product():
    form = ProductForm()
    form.category.choices = product_category_choices()
    if form.validate_on_submit():
        try:
            name = form.name.data
            current_price_val = form.price.data # Already a float due to FloatField
            original_price_val = form.original_price.data if form.original_price.data is not None else None
            description = form.description.data
            category = form.category.data
            stock_val = form.stock.data # Already an int due to IntegerField
            
            image_filename = None # To store the filename for the database
            default_image_path_segment = 'uploads/default.jpg'

            if form.image.data:
                image_file = form.image.data
                if image_file.filename == '': # No file selected
                    image_filename = default_image_path_segment
                elif valid_photo_upload(image_file):
                    filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                    try:
                        save_clean_image(image_file, os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                        image_filename = f"uploads/{filename}"
                    except ValueError:
                        flash('That photo could not be read. Using the default image.', 'warning')
                        image_filename = default_image_path_segment
                else: # File selected, but not allowed type
                    flash('Invalid image file type. Using default image if none previously set.', 'warning')
                    image_filename = default_image_path_segment # Fallback or keep existing if editing
            else: # No new image file provided
                 image_filename = default_image_path_segment


            # Logic for original_price: only set if it's greater than current_price
            db_original_price = None
            if original_price_val is not None:
                if original_price_val > current_price_val:
                    db_original_price = original_price_val
                elif original_price_val == current_price_val: # If same, no effective discount
                     db_original_price = None
                else: # original_price cannot be less than current_price if it's a "was" price
                    flash('Original price cannot be less than the current (sale) price. Original price not saved.', 'warning')
            
            with get_db_connection() as conn:
                cur = conn.execute('''
                    INSERT INTO products (name, price, original_price, description, category, stock, image, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (name, current_price_val, db_original_price, description, category, stock_val, image_filename))
                new_product_id = cur.lastrowid
                apply_price_change(conn, new_product_id, None, current_price_val, db_original_price, stock_val)
                conn.commit()
            save_product_photos(new_product_id)
            flash('Product added successfully!', 'success')
            log_activity("Admin added product", user_id=current_user.id, details=f"Product Name: {name}")
            return redirect(url_for('admin.admin_products'))
        except ValueError as ve: # Should be less common with typed form fields
            flash(f'Invalid data format: {str(ve)}', 'danger')
        except Exception as e:
            current_app.logger.error(f"Error adding product: {e}", exc_info=True)
            flash(f'Error adding product: {str(e)}', 'danger')
            log_activity("Admin add product failed", user_id=current_user.id, details=str(e))
            
    return render_template('admin/add_edit_product.html', form=form, title='Add Product', action='Add',
                           photos=[], max_photos=MAX_PRODUCT_PHOTOS, max_upload_mb=max_upload_mb())


@bp.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_product(product_id):
    product = None
    with get_db_connection() as conn: # Fetch product for GET and for current image path
        product_data_raw = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
        if product_data_raw:
            product = dict(product_data_raw) # Convert to dict for easier manipulation

    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('admin.admin_products'))

    form = ProductForm() # Create empty form first
    form.category.choices = product_category_choices(product.get('category'))

    if form.validate_on_submit(): # POST request
        try:
            name = form.name.data
            current_price_val = form.price.data
            original_price_val = form.original_price.data if form.original_price.data is not None else None
            description = form.description.data
            category = form.category.data
            stock_val = form.stock.data
            
            image_filename = product['image'] # Keep old image by default

            if form.image.data and form.image.data.filename: # Check if a new file was actually uploaded
                image_file = form.image.data
                if valid_photo_upload(image_file):
                    # Optional: Delete old image file if it's not default.jpg and different
                    if product['image'] and product['image'] != 'uploads/default.jpg':
                        old_image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], os.path.basename(product['image']))
                        if os.path.exists(old_image_path):
                            try:
                                os.remove(old_image_path)
                            except OSError as ose:
                                current_app.logger.error(f"Error deleting old image {old_image_path}: {ose}")
                    
                    filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                    try:
                        save_clean_image(image_file, os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                        image_filename = f"uploads/{filename}"
                    except ValueError:
                        flash('That photo could not be read. Current image retained.', 'warning')
                else:
                    flash('Invalid new image file type. Current image retained.', 'warning')
            
            db_original_price = None
            if original_price_val is not None:
                if original_price_val > current_price_val:
                    db_original_price = original_price_val
                elif original_price_val == current_price_val:
                     db_original_price = None
                else:
                    flash('Original price cannot be less than the current (sale) price. Original price not saved as entered.', 'warning')
                    # Keep existing db_original_price if the new one is invalid, or set to None
                    db_original_price = product.get('original_price') # Revert to product's current original_price or None

            with get_db_connection() as conn:
                conn.execute('''
                    UPDATE products SET name = ?, price = ?, original_price = ?, description = ?,
                    category = ?, stock = ?, image = ? WHERE id = ?
                ''', (name, current_price_val, db_original_price, description, category, stock_val, image_filename, product_id))
                alerts = apply_price_change(conn, product_id, product, current_price_val, db_original_price, stock_val)
                conn.commit()
            save_product_photos(product_id)
            send_wishlist_alerts(product_id, alerts)
            flash('Product updated successfully!', 'success')
            log_activity("Admin edited product", user_id=current_user.id, details=f"Product ID: {product_id}")
            return redirect(url_for('admin.admin_products'))
        except ValueError as ve:
            flash(f'Invalid data format: {str(ve)}', 'danger')
        except Exception as e:
            current_app.logger.error(f"Error updating product {product_id}: {e}", exc_info=True)
            flash(f'Error updating product: {str(e)}', 'danger')
            log_activity("Admin edit product failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    
    elif request.method == 'GET': # Populate form for GET request
        form.name.data = product['name']
        form.price.data = product['price']
        form.original_price.data = product['original_price']
        form.description.data = product['description']
        form.category.data = product['category']
        form.stock.data = product['stock']
        # form.image.data is not set for FileField on GET

    with get_db_connection() as conn:
        photos = product_photos(conn, product_id, product.get('image'))
    from shop.pricing import VERIFY_DAYS, check_was_price, honest_discount
    discount_status = None
    if product.get('original_price') and product['original_price'] > product['price']:
        with get_db_connection() as conn:
            _, days = check_was_price(conn, product_id, product['original_price'])
        discount_status = {'shown': bool(honest_discount(product)), 'verified': bool(product.get('was_price_verified')),
                           'days': round(days, 1), 'need': VERIFY_DAYS}
    return render_template('admin/add_edit_product.html', form=form, title='Edit Product', product=product, action='Edit',
                           discount_status=discount_status,
                           photos=photos, max_photos=MAX_PRODUCT_PHOTOS, max_upload_mb=max_upload_mb())


@bp.route('/products/delete/<int:product_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_product(product_id):
    # Ensure CSRF token is validated (Flask-WTF handles this if the form is submitted via a POST request)
    # This route is called from a form with a CSRF token in the template.
    try:
        with get_db_connection() as conn:
            # Past orders reference this product, so it can't be removed (check before touching the image file)
            in_orders = conn.execute("SELECT 1 FROM order_items WHERE product_id = ? LIMIT 1", (product_id,)).fetchone()
            if in_orders:
                flash('This product appears in past orders and cannot be deleted. Set its stock to 0 to stop selling it.', 'warning')
                return redirect(url_for('admin.admin_products'))
            product_data = conn.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
            for extra in conn.execute('SELECT path FROM product_images WHERE product_id = ?', (product_id,)).fetchall():
                _delete_upload(extra['path'])
            if product_data:
                # Delete associated image file if not default
                if product_data['image'] and product_data['image'] != 'uploads/default.jpg':
                    image_filename = product_data['image'].split('/')[-1] # Get filename part
                    image_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], image_filename)
                    if os.path.exists(image_file_path):
                        try:
                            os.remove(image_file_path)
                        except OSError as e:
                            current_app.logger.error(f"Error deleting image file {image_file_path}: {e}")
                            flash(f"Could not delete image file: {e}", "warning")
            
            # The 'ON DELETE CASCADE' or 'ON DELETE SET NULL' in your DB schema 
            # should handle carts, reviews, wishlists. Order_items might be set to SET NULL.
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        flash('Product deleted successfully.', 'success')
        log_activity("Admin deleted product", user_id=current_user.id, details=f"Product ID: {product_id}")
    except IntegrityError as e: 
        current_app.logger.error(f"Integrity error deleting product {product_id}: {e}")
        flash(f'Error deleting product. It might be referenced in existing orders that prevent deletion. ({str(e)})', 'danger')
    except Exception as e:
        current_app.logger.error(f"Error deleting product {product_id}: {e}")
        flash(f'Error deleting product: {str(e)}', 'danger')
        log_activity("Admin delete product failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(url_for('admin.admin_products'))


@bp.route('/promotions')
@login_required
@admin_required
def admin_promotions():
    with get_db_connection() as conn:
        promotions_data = conn.execute('SELECT * FROM promotions ORDER BY display_order ASC, created_at DESC').fetchall()
    
    promotions_list = []
    for row in promotions_data:
        promo_dict = dict(row)
        if promo_dict.get('created_at') and isinstance(promo_dict['created_at'], str):
            try:
                promo_dict['created_at'] = datetime.strptime(promo_dict['created_at'], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                current_app.logger.warning(f"Could not parse created_at for promotion ID: {promo_dict['id']}")
        promotions_list.append(promo_dict)

    return render_template('admin/promotions_list.html', promotions=promotions_list, title="Manage Promotions")


@bp.route('/promotions/add', methods=['GET', 'POST'])
@login_required
@admin_required # Assuming you have this decorator
def admin_add_promotion():
    form = PromotionForm()

    if form.validate_on_submit():
        image_filename_for_db = None
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if valid_photo_upload(image_file): # Make sure allowed_file is defined
                filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                # Ensure PROMOTIONS_UPLOAD_FOLDER is defined in current_app.config
                promotions_folder = current_app.config.get('PROMOTIONS_UPLOAD_FOLDER', os.path.join(current_app.static_folder, 'uploads', 'promotions'))
                os.makedirs(promotions_folder, exist_ok=True) # Ensure folder exists
                save_path = os.path.join(promotions_folder, filename)
                try:
                    save_clean_image(image_file, save_path)
                    image_filename_for_db = f"uploads/promotions/{filename}" # Path relative to static/
                except Exception as e_save:
                    flash(f"Error saving promotion image: {str(e_save)}", "danger")
                    current_app.logger.error(f"Error saving promotion image: {e_save}")
            else:
                flash('Invalid image file type for promotion. Image not saved.', 'warning')
        
        # Prepare data for database
        promo_data_to_save = {
            "title": form.title.data, "image_path": image_filename_for_db, 
            "target_url": form.target_url.data, "alt_text": form.alt_text.data,
            "original_price": form.original_price.data, "promo_price": form.promo_price.data,
            "discount_text": form.discount_text.data,
            "is_active": 1 if form.is_active.data else 0,
            "display_order": form.display_order.data if form.display_order.data is not None else 0
        }

        try:
            with get_db_connection() as conn:
                conn.execute('''
                    INSERT INTO promotions (title, image_path, target_url, alt_text, 
                                            original_price, promo_price, discount_text, 
                                            is_active, display_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', tuple(promo_data_to_save[k] for k in ('title', 'image_path', 'target_url', 'alt_text', 'original_price',
                                                          'promo_price', 'discount_text', 'is_active', 'display_order')))
                conn.commit()
            flash('Promotion added successfully!', 'success')
            # log_activity("Admin added promotion", user_id=current_user.id, details=f"Title: {form.title.data}")
            return redirect(url_for('admin.admin_promotions'))
        except DatabaseError as e_sql:
            current_app.logger.error(f"Database error adding promotion: {e_sql}")
            flash(f'Database error adding promotion: {str(e_sql)}', 'danger')
        except Exception as e_generic:
            current_app.logger.error(f"Unexpected error adding promotion: {e_generic}", exc_info=True)
            flash(f'An unexpected error occurred: {str(e_generic)}', 'danger')

    return render_template('admin/add_edit_promotion.html', form=form, title='Add Promotion', action='Add', promotion=None,
                           max_upload_mb=max_upload_mb())


@bp.route('/promotions/edit/<int:promo_id>', methods=['GET', 'POST'])
@login_required
@admin_required # Assuming you have this decorator
def admin_edit_promotion(promo_id):
    promotion_data_dict = None
    with get_db_connection() as conn:
        promotion_data_raw = conn.execute('SELECT * FROM promotions WHERE id = ?', (promo_id,)).fetchone()
        if promotion_data_raw:
            promotion_data_dict = dict(promotion_data_raw)

    if not promotion_data_dict:
        flash('Promotion not found.', 'danger')
        return redirect(url_for('admin.admin_promotions'))

    form = PromotionForm() # Create empty form first
    

    if form.validate_on_submit(): # POST request
        new_image_filename_for_db = promotion_data_dict['image_path'] # Keep old image by default

        if form.image.data and form.image.data.filename: 
            image_file = form.image.data
            if valid_photo_upload(image_file):
                if promotion_data_dict['image_path']: # If there was an old image
                    # Define BASE_DIR or get from current_app.config
                    base_dir = os.path.dirname(current_app.static_folder) 
                    old_image_full_path = os.path.join(base_dir, 'static', promotion_data_dict['image_path'])
                    if os.path.exists(old_image_full_path) and "default" not in promotion_data_dict['image_path'].lower() : # Don't delete default
                        try:
                            os.remove(old_image_full_path)
                        except OSError as e_remove:
                            current_app.logger.error(f"Error deleting old promotion image {old_image_full_path}: {e_remove}")
                
                filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                promotions_folder = current_app.config.get('PROMOTIONS_UPLOAD_FOLDER', os.path.join(current_app.static_folder, 'uploads', 'promotions'))
                os.makedirs(promotions_folder, exist_ok=True)
                save_path = os.path.join(promotions_folder, filename)
                try:
                    save_clean_image(image_file, save_path)
                    new_image_filename_for_db = f"uploads/promotions/{filename}"
                except Exception as e_save:
                    flash(f"Error saving new promotion image: {str(e_save)}", "danger")
                    current_app.logger.error(f"Error saving new promotion image: {e_save}")
            else:
                flash('Invalid new image file type. Current image retained.', 'warning')
        
        # Prepare data for database update
        promo_data_to_update = {
            "title": form.title.data, "image_path": new_image_filename_for_db,
            "target_url": form.target_url.data, "alt_text": form.alt_text.data,
            "original_price": form.original_price.data, "promo_price": form.promo_price.data,
            "discount_text": form.discount_text.data,
            "is_active": 1 if form.is_active.data else 0,
            "display_order": form.display_order.data if form.display_order.data is not None else 0,
            "promo_id": promo_id
        }

        try:
            with get_db_connection() as conn:
                conn.execute('''
                    UPDATE promotions SET title=?, image_path=?, target_url=?, alt_text=?, original_price=?,
                                          promo_price=?, discount_text=?, is_active=?, display_order=?
                    WHERE id=?
                ''', tuple(promo_data_to_update[k] for k in ('title', 'image_path', 'target_url', 'alt_text', 'original_price',
                                                            'promo_price', 'discount_text', 'is_active', 'display_order', 'promo_id')))
                conn.commit()
            flash('Promotion updated successfully!', 'success')
            # log_activity("Admin edited promotion", user_id=current_user.id, details=f"Promotion ID: {promo_id}")
            return redirect(url_for('admin.admin_promotions'))
        except DatabaseError as e_sql:
            current_app.logger.error(f"Database error updating promotion {promo_id}: {e_sql}")
            flash(f'Database error updating promotion: {str(e_sql)}', 'danger')
        except Exception as e_generic:
            current_app.logger.error(f"Unexpected error updating promotion {promo_id}: {e_generic}", exc_info=True)
            flash(f'An unexpected error occurred: {str(e_generic)}', 'danger')
            
    elif request.method == 'GET': # Populate form for GET request
        form.title.data = promotion_data_dict['title']
        form.target_url.data = promotion_data_dict['target_url']
        form.alt_text.data = promotion_data_dict['alt_text']
        form.original_price.data = promotion_data_dict.get('original_price') # Use .get() for safety
        form.promo_price.data = promotion_data_dict.get('promo_price')
        form.discount_text.data = promotion_data_dict.get('discount_text')
        form.is_active.data = bool(promotion_data_dict['is_active'])
        form.display_order.data = promotion_data_dict['display_order']
        return render_template(
            'admin/add_edit_promotion.html',
            form=form,
            title='Edit Promotion',
            action='Edit',
            promotion=promotion_data_dict,  # Pass promotion_data_dict for displaying current image
            max_upload_mb=max_upload_mb()
        )

    # POST that failed validation (or failed to save): show the form again with its errors
    return render_template(
        'admin/add_edit_promotion.html',
        form=form,
        title='Edit Promotion',
        action='Edit',
        promotion=promotion_data_dict,
        max_upload_mb=max_upload_mb()
    )


@bp.route('/promotions/delete/<int:promo_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_promotion(promo_id):
    try:
        with get_db_connection() as conn:
            promotion_data = conn.execute("SELECT image_path FROM promotions WHERE id = ?", (promo_id,)).fetchone()
            if promotion_data and promotion_data['image_path']:
                image_file_path = os.path.join(current_app.static_folder, promotion_data['image_path'])
                if os.path.exists(image_file_path):
                    try:
                        os.remove(image_file_path)
                    except OSError as e:
                        current_app.logger.error(f"Error deleting promotion image file {image_file_path}: {e}")
                        flash(f"Could not delete image file: {e}", "warning")
            
            conn.execute("DELETE FROM promotions WHERE id = ?", (promo_id,))
            conn.commit()
        flash('Promotion deleted successfully.', 'success')
        log_activity("Admin deleted promotion", user_id=current_user.id, details=f"Promotion ID: {promo_id}")
    except Exception as e:
        current_app.logger.error(f"Error deleting promotion {promo_id}: {e}")
        flash(f'Error deleting promotion: {str(e)}', 'danger')
    return redirect(url_for('admin.admin_promotions'))
