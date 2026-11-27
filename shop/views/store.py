"""Shop front: home page, product pages, reviews, search."""
import sqlite3
from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from shop.db import get_db_connection
from shop.forms import ReviewForm
from shop.helpers import product_photos
from shop.security import log_activity


bp = Blueprint('store', __name__)


@bp.route('/')  # public: visitors can browse without an account # Keep this if you want the homepage to require login
def index():
    category_filter = request.args.get('category', 'All')
    page = request.args.get('page', 1, type=int)
    per_page = 9  # Number of products for the main paginated grid

    # Initialize lists
    active_promotions = []
    products = []
    featured_products = [] # Initialize featured_products list
    total_products = 0

    try:
        with get_db_connection() as conn:
            # Fetch active promotions
            active_promotions_raw = conn.execute(
                'SELECT * FROM promotions WHERE is_active = 1 ORDER BY display_order ASC, id DESC'
            ).fetchall()
            for promo_row in active_promotions_raw:
                active_promotions.append(dict(promo_row))

            # --- Product Fetching Logic (Main paginated list) ---
            # (Your original product fetching logic for the main grid)
            base_products_query_select = 'SELECT *' # Explicitly select columns
            base_products_query_from = 'FROM products'
            base_count_query = 'SELECT COUNT(id) FROM products'
            
            filter_conditions = []
            query_values = []

            if category_filter != 'All':
                filter_conditions.append('category = ?')
                query_values.append(category_filter)
            
            # search_term = request.args.get('q')
            # if search_term:
            #     filter_conditions.append('(name LIKE ? OR description LIKE ?)')
            #     query_values.extend([f'%{search_term}%', f'%{search_term}%'])

            where_clause = ''
            if filter_conditions:
                where_clause = ' WHERE ' + ' AND '.join(filter_conditions)
            
            final_base_count_query = base_count_query + where_clause
            total_products_result = conn.execute(final_base_count_query, tuple(query_values)).fetchone()
            total_products = total_products_result[0] if total_products_result and total_products_result[0] is not None else 0

            offset = (page - 1) * per_page
            products_sql = f'{base_products_query_select} {base_products_query_from}{where_clause} ORDER BY added_at DESC LIMIT ? OFFSET ?'
            raw_products = conn.execute(products_sql, tuple(query_values + [per_page, offset])).fetchall()
            
            for prod_row in raw_products:
                prod_dict = dict(prod_row)
                if prod_dict.get('added_at') and isinstance(prod_dict['added_at'], str):
                    try:
                        prod_dict['added_at'] = datetime.strptime(prod_dict['added_at'], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        # Ensure current_app.logger is correctly accessed (e.g., from current_app or app instance)
                        # For example, using current_app:
                        # from flask import current_app
                        # current_app.logger.warning(...)
                        # Or if 'app' is your Flask instance:
                        # current_app.logger.warning(...)
                        # For now, using print as a fallback if logger isn't set up in this snippet's context
                        print(f"Warning: Could not parse added_at string: {prod_dict['added_at']} for product ID {prod_dict['id']} on index page")
                products.append(prod_dict)
            # --- End Main Product Fetching Logic ---

            # --- NEW: Fetch products for the "Featured/Horizontal Scroll" section ---
            # Example: Fetch first 8 most recent products.
            # Customize this query (e.g., WHERE is_featured = 1) as needed.
            featured_products_raw = conn.execute(
                '''SELECT * FROM products
                   WHERE stock > 0
                   ORDER BY (original_price IS NOT NULL AND original_price > price) DESC, added_at DESC
                   LIMIT 8'''  # deals first, then newest in-stock items
            ).fetchall()
            
            for prod_row_featured in featured_products_raw:
                prod_dict_featured = dict(prod_row_featured)
                # Date parsing for 'added_at' if you display it in the card for these featured products
                if prod_dict_featured.get('added_at') and isinstance(prod_dict_featured['added_at'], str):
                    try:
                        prod_dict_featured['added_at'] = datetime.strptime(prod_dict_featured['added_at'], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        print(f"Warning: Could not parse added_at for featured product ID {prod_dict_featured['id']}")
                featured_products.append(prod_dict_featured)
            # --- END NEW ---

            # Categories for sidebar (using categories_for_navbar from context_processor is often sufficient)
            # No need to redefine if your context_processor handles 'categories_for_navbar'

    except sqlite3.Error as e: # Catch potential database errors
        print(f"Database error in index route: {e}") # Log this properly in a real app
        # Initialize to empty lists if DB error occurs, so template doesn't break
        active_promotions = []
        products = []
        featured_products = []
        total_products = 0
        # flash("Could not retrieve data. Please try again later.", "danger") # Inform user

    total_pages = (total_products + per_page - 1) // per_page if total_products > 0 else 0
    
    pagination_query_params = {}
    if category_filter != 'All':
        pagination_query_params['category'] = category_filter
    # if search_term: 
    #     pagination_query_params['q'] = search_term

    return render_template(
        'index.html',
        products=products,                      # Main paginated product list
        featured_products=featured_products,    # NEW: For the horizontal scroll section
        # categories=page_categories,           # Only if you fetch page_categories specifically here
        current_category=category_filter,
        current_page=page,
        total_pages=total_pages,
        total_products=total_products,
        title='Home' if category_filter == 'All' else category_filter,
        endpoint='store.index',                       # For pagination
        query_params=pagination_query_params,   # For pagination
        promotions=active_promotions            # For the carousel
    )


@bp.route('/product/<int:product_id>')
def product_details(product_id):
    review_form = ReviewForm()
    product_data_for_template = None # Initialize
    processed_reviews = []       # Initialize

    try:
        with get_db_connection() as conn:
            product_raw = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            if not product_raw:
                flash('Product not found.', 'danger')
                return redirect(url_for('store.index'))
            
            # Process product data (e.g., if it also has dates that need parsing for this page)
            product_data_for_template = dict(product_raw) # Convert to dict
            # Example: If product also had an 'added_at' to display and parse:
            # if product_data_for_template.get('added_at') and isinstance(product_data_for_template['added_at'], str):
            #     try:
            #         product_data_for_template['added_at'] = datetime.strptime(product_data_for_template['added_at'], '%Y-%m-%d %H:%M:%S')
            #     except ValueError:
            #         current_app.logger.warning(f"Could not parse added_at for product {product_id} in product_details")


            reviews_raw = conn.execute('''
                SELECT r.rating, r.comment, r.created_at, u.username
                FROM reviews r
                JOIN users u ON r.user_id = u.id
                WHERE r.product_id = ?
                ORDER BY r.created_at DESC
            ''', (product_id,)).fetchall()

            # ---- START MODIFICATION for reviews ----
            for review_row in reviews_raw:
                review_dict = dict(review_row) # Convert sqlite3.Row to a mutable dictionary
                if review_dict.get('created_at') and isinstance(review_dict['created_at'], str):
                    try:
                        # Attempt to parse the datetime string. Adjust format if necessary.
                        # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                        review_dict['created_at'] = datetime.strptime(review_dict['created_at'], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        # Log the error and decide how to handle (e.g., keep as string, set to None)
                        # current_app.logger.warning(f"Could not parse created_at string for review: {review_dict['created_at']}")
                        print(f"Warning: Could not parse created_at string for review: {review_dict['created_at']}") # Fallback
                        # review_dict['created_at'] = None # Or keep as string and let template handle N/A
                processed_reviews.append(review_dict)
            # ---- END MODIFICATION for reviews ----
            
    except sqlite3.Error as e:
        flash("A database error occurred while fetching product details.", "danger")
        # Log the error: current_app.logger.error(f"DB error in product_details for {product_id}: {e}")
        print(f"DB error in product_details for {product_id}: {e}") # Fallback
        return redirect(url_for('store.index')) # Or an error page

    if not product_data_for_template: # Should have been caught by 'if not product_raw' but as a safeguard
        flash('Product data could not be loaded.', 'danger')
        return redirect(url_for('store.index'))

    with get_db_connection() as conn:
        photos = product_photos(conn, product_id, product_data_for_template.get('image')) or [None]
    return render_template(
        'product_details.html',
        photos=photos,
        product=product_data_for_template,  # Pass the (potentially processed) product dict
        reviews=processed_reviews,          # Pass the list of processed review dicts
        review_form=review_form,
        title=product_data_for_template['name']
    )


@bp.route('/product/<int:product_id>/review', methods=['POST'])
@login_required
def add_review(product_id):
    form = ReviewForm()
    if form.validate_on_submit(): # Using WTForms for validation
        rating = form.rating.data # Expecting 1-5, will need to validate this
        comment = form.comment.data
        try:
            # Validate rating (e.g., if it's from a select field, it might already be somewhat validated)
            rating_int = int(rating)
            if not (1 <= rating_int <= 5):
                flash('Rating must be between 1 and 5.', 'danger')
                return redirect(url_for('store.product_details', product_id=product_id))

            with get_db_connection() as conn:
                # Optional: Check if user purchased the product
                # purchased = conn.execute('''
                #     SELECT 1 FROM order_items oi
                #     JOIN orders o ON oi.order_id = o.id
                #     WHERE o.user_id = ? AND oi.product_id = ?
                # ''', (current_user.id, product_id)).fetchone()
                # if not purchased:
                #     flash('You must purchase this product to review it.', 'warning')
                #     return redirect(url_for('store.product_details', product_id=product_id))

                conn.execute('''
                    INSERT INTO reviews (user_id, product_id, rating, comment)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, product_id) DO UPDATE SET
                    rating=excluded.rating, comment=excluded.comment, created_at=CURRENT_TIMESTAMP
                ''', (current_user.id, product_id, rating_int, comment))
                conn.commit()
            flash('Review submitted successfully!', 'success')
            log_activity("Review added", user_id=current_user.id, details=f"Product ID: {product_id}, Rating: {rating_int}")
        except ValueError:
            flash('Invalid rating value.', 'danger')
        except sqlite3.IntegrityError: # Should be handled by ON CONFLICT now
            flash('You have already reviewed this product. Your review has been updated.', 'info')
        except Exception as e:
            flash('Sorry, your review could not be saved. Please try again.', 'danger')
            log_activity("Review submission failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    else:
        # Collect form errors if any and flash them
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')
    return redirect(url_for('store.product_details', product_id=product_id))


@bp.route('/search')  # public: visitors can browse without an account
def search():
    query = request.args.get('query', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10 # Number of search results per page

    if not query:
        # If query is empty, maybe redirect to index or show a message directly
        flash("Please enter a search term.", "info")
        return render_template('search_results.html', query=query, results=[], title='Search', total_results=0, total_pages=0, endpoint='store.search', query_params={'query': query})


    with get_db_connection() as conn:
        search_term = f'%{query}%'
        # Adjusted to search only in name and description for more relevance usually
        count_query_sql = "SELECT COUNT(id) FROM products WHERE name LIKE ? OR description LIKE ?"
        results_query_sql = "SELECT * FROM products WHERE name LIKE ? OR description LIKE ? ORDER BY name LIMIT ? OFFSET ?"
        
        params = (search_term, search_term)
        
        total_results_data = conn.execute(count_query_sql, params).fetchone()
        total_results = total_results_data[0] if total_results_data else 0
        
        results = conn.execute(results_query_sql, params + (per_page, (page - 1) * per_page)).fetchall()

    total_pages = (total_results + per_page - 1) // per_page if total_results > 0 else 0
    
    if query: # Only log if there was an actual query
        log_activity("Search performed", user_id=current_user.id if current_user.is_authenticated else None, details=f"Query: {query}")

    # Prepare query_params for pagination
    pagination_query_params = {'query': query}

    return render_template(
        'search_results.html',
        query=query,
        results=results,
        current_page=page,
        total_pages=total_pages,
        total_results=total_results,
        title=f'Search for "{query}"',
        endpoint='store.search',                       # <-- ADDED for pagination
        query_params=pagination_query_params    # <-- ADDED for pagination
    )
