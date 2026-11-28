"""Admin dashboard, reports and activity log."""
import json
from datetime import datetime, time, timedelta, timezone
from flask import current_app, flash, render_template, request
from flask_login import login_required
from shop.db import DatabaseError, get_db_connection
from shop.helpers import SHOP_TZ
from shop.security import admin_required

from shop.admin import bp

# Order statuses that are not money coming in (everything else counts towards "sales today")
NOT_A_SALE = ('Cancelled', 'Payment Failed', 'Refunded')


def today_start_utc(now=None):
    """Midnight today in the shop's time zone, as a UTC 'YYYY-MM-DD HH:MM:SS' string (how order times are stored)."""
    local_now = (now or datetime.now(timezone.utc)).astimezone(SHOP_TZ)
    midnight = datetime.combine(local_now.date(), time.min, tzinfo=SHOP_TZ)
    return midnight.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


@bp.route('/dashboard')
@login_required
@admin_required
def admin_dashboard():
    with get_db_connection() as conn:
        # Total Sales (Paid)
        total_sales_data = conn.execute("SELECT SUM(total_amount) FROM orders WHERE status = 'Paid'").fetchone()
        total_sales = total_sales_data[0] if total_sales_data and total_sales_data[0] is not None else 0

        # Total Users
        total_users_data = conn.execute('SELECT COUNT(id) FROM users').fetchone()
        total_users = total_users_data[0] if total_users_data else 0

        # Total Products
        total_products_data = conn.execute('SELECT COUNT(id) FROM products').fetchone()
        total_products = total_products_data[0] if total_products_data else 0

        # Pending Orders Count (includes 'Pending', 'Pending Payment', 'Processing')
        pending_orders_data = conn.execute(
            "SELECT COUNT(id) FROM orders WHERE status IN ('Pending', 'Pending Payment', 'Processing')"
        ).fetchone()
        pending_orders_count = pending_orders_data[0] if pending_orders_data else 0
        
        # Individual counts for the alert buttons
        pending_count_data = conn.execute("SELECT COUNT(id) FROM orders WHERE status='Pending'").fetchone()
        pending_count = pending_count_data[0] if pending_count_data else 0
        
        pending_payment_count_data = conn.execute("SELECT COUNT(id) FROM orders WHERE status='Pending Payment'").fetchone()
        pending_payment_count = pending_payment_count_data[0] if pending_payment_count_data else 0
        
        processing_count_data = conn.execute("SELECT COUNT(id) FROM orders WHERE status='Processing'").fetchone()
        processing_count = processing_count_data[0] if processing_count_data else 0

        # Today (shop time zone) at a glance. Order times are stored in UTC, so compare against
        # the UTC moment of local midnight - a plain text timestamp works on SQLite and PostgreSQL.
        # Sales count every order that is still going ahead: cash on delivery orders are never
        # marked 'Paid', so only cancelled / failed / refunded orders are left out.
        since = today_start_utc()
        today_row = conn.execute(
            f"""SELECT COUNT(id) AS orders,
                       SUM(CASE WHEN status NOT IN ({','.join('?' * len(NOT_A_SALE))}) THEN total_amount ELSE 0 END) AS sales
                FROM orders WHERE order_date >= ?""",
            (*NOT_A_SALE, since)).fetchone()
        today_orders = today_row['orders'] or 0
        today_sales = today_row['sales'] or 0
        out_of_stock_count = conn.execute('SELECT COUNT(id) FROM products WHERE stock <= 0').fetchone()[0] or 0


        # Recent Orders (Last 5)
        recent_orders_raw = conn.execute('''
            SELECT o.id, u.username, o.total_amount, o.order_date, o.status
            FROM orders o JOIN users u ON o.user_id = u.id
            ORDER BY o.order_date DESC LIMIT 5
        ''').fetchall()
        processed_recent_orders = []
        for row in recent_orders_raw:
            order_dict = dict(row)
            if order_dict.get('order_date') and isinstance(order_dict['order_date'], str):
                try:
                    order_dict['order_date'] = datetime.strptime(order_dict['order_date'], '%Y-%m-%d %H:%M:%S')
                    # Create a pre-formatted string version for easier use in template if needed
                    order_dict['order_date_formatted'] = order_dict['order_date'].strftime('%b %d, %Y %H:%M')
                except ValueError:
                    current_app.logger.warning(f"Dashboard: Could not parse order_date {order_dict['order_date']}")
                    order_dict['order_date_formatted'] = order_dict['order_date'] # Fallback
            else: # If already a datetime object or None
                if order_dict.get('order_date'):
                    try:
                        order_dict['order_date_formatted'] = order_dict['order_date'].strftime('%b %d, %Y %H:%M')
                    except AttributeError: # Not a datetime object
                         order_dict['order_date_formatted'] = str(order_dict.get('order_date', 'N/A'))
                else:
                    order_dict['order_date_formatted'] = 'N/A'

            processed_recent_orders.append(order_dict)

        # Top Selling Products
        top_products = conn.execute('''
            SELECT p.name, SUM(oi.quantity) as total_sold
            FROM order_items oi JOIN products p ON oi.product_id = p.id
            JOIN orders o ON oi.order_id = o.id WHERE o.status = 'Paid'
            GROUP BY p.id ORDER BY total_sold DESC LIMIT 5
        ''').fetchall()

        # Promotions Overview
        active_promos_data = conn.execute('SELECT COUNT(id) FROM promotions WHERE is_active = 1').fetchone()
        active_promos_count = active_promos_data[0] if active_promos_data else 0
        total_promos_data = conn.execute('SELECT COUNT(id) FROM promotions').fetchone()
        total_promos_count = total_promos_data[0] if total_promos_data else 0

        # Active Promotions List for Dashboard
        dashboard_active_promotions_raw = conn.execute('''
            SELECT id, title, image_path, target_url, original_price, promo_price, discount_text, display_order 
            FROM promotions WHERE is_active = 1 
            ORDER BY display_order ASC, created_at DESC LIMIT 3 
        ''').fetchall()
        dashboard_active_promotions = [dict(row) for row in dashboard_active_promotions_raw]

        # Low Stock Alerts
        low_stock_threshold = current_app.config.get('LOW_STOCK_THRESHOLD', 5)
        low_stock_products_count_data = conn.execute(
            'SELECT COUNT(id) FROM products WHERE stock > 0 AND stock <= ?', (low_stock_threshold,)
        ).fetchone()
        low_stock_products_count = low_stock_products_count_data[0] if low_stock_products_count_data else 0
        
        low_stock_products_list = conn.execute(
           'SELECT id, name, stock FROM products WHERE stock > 0 AND stock <= ? ORDER BY stock ASC LIMIT 3',
           (low_stock_threshold,)
        ).fetchall()

               # ...existing code...
        recent_activities_raw = conn.execute('''
            SELECT al.id, al.action, al.details, al.timestamp, u.username 
            FROM activity_logs al 
            LEFT JOIN users u ON al.user_id = u.id 
            ORDER BY al.timestamp DESC LIMIT 7
        ''').fetchall()
        # ...existing code...
        processed_recent_activities = []
        for activity_row in recent_activities_raw:
            activity_dict = dict(activity_row)
            if activity_dict.get('timestamp') and isinstance(activity_dict['timestamp'], str):
                try:
                    activity_dict['timestamp'] = datetime.strptime(activity_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
                    # Create a pre-formatted string version
                    activity_dict['timestamp_formatted'] = activity_dict['timestamp'].strftime('%b %d, %H:%M')
                except ValueError:
                    current_app.logger.warning(f"Dashboard: Could not parse activity timestamp {activity_dict['timestamp']}")
                    activity_dict['timestamp_formatted'] = activity_dict['timestamp'] # Fallback
            else: # If already a datetime object or None
                if activity_dict.get('timestamp'):
                    try:
                        activity_dict['timestamp_formatted'] = activity_dict['timestamp'].strftime('%b %d, %H:%M')
                    except AttributeError:
                        activity_dict['timestamp_formatted'] = str(activity_dict.get('timestamp', 'N/A'))
                else:
                    activity_dict['timestamp_formatted'] = 'N/A'
            processed_recent_activities.append(activity_dict)
        # --- END NEW ---

    return render_template(
        'admin/dashboard.html',
        title='Admin Dashboard',
        total_sales=total_sales,
        total_users=total_users,
        total_products=total_products,
        pending_orders_count=pending_orders_count,
        # Pass individual counts for alert buttons
        pending_count=pending_count,
        pending_payment_count=pending_payment_count,
        processing_count=processing_count,
        recent_orders=processed_recent_orders,
        top_products=top_products,
        active_promos_count=active_promos_count,
        total_promos_count=total_promos_count,
        dashboard_active_promotions=dashboard_active_promotions,
        low_stock_products_count=low_stock_products_count,
        low_stock_products_list=low_stock_products_list,
        low_stock_threshold=low_stock_threshold,
        processed_recent_activities=processed_recent_activities,
        today_orders=today_orders,
        today_sales=today_sales,
        out_of_stock_count=out_of_stock_count,
    )


@bp.route('/activity_logs')
@login_required
@admin_required
def admin_activity_logs():
    page = request.args.get('page', 1, type=int)
    per_page = 25  # Logs can be numerous

    with get_db_connection() as conn:
        total_logs_data = conn.execute('SELECT COUNT(id) FROM activity_logs').fetchone()
        total_logs_count = total_logs_data[0] if total_logs_data else 0

        offset = (page - 1) * per_page
        raw_logs = conn.execute('''
            SELECT al.id, u.username, al.action, al.details, al.timestamp
            FROM activity_logs al
            LEFT JOIN users u ON al.user_id = u.id
            ORDER BY al.timestamp DESC
            LIMIT ? OFFSET ?
        ''', (per_page, offset)).fetchall()

        # ---- START MODIFICATION ----
        logs = []
        for row in raw_logs:
            log_dict = dict(row) # Convert the row to a mutable dictionary
            if log_dict.get('timestamp') and isinstance(log_dict['timestamp'], str):
                try:
                    # Attempt to parse the datetime string. Adjust format if necessary.
                    # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                    log_dict['timestamp'] = datetime.strptime(log_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    current_app.logger.warning(f"Could not parse timestamp string: {log_dict['timestamp']} for log ID: {log_dict['id']}")
                    # log_dict['timestamp'] = None # Or keep as string
            logs.append(log_dict)
        # ---- END MODIFICATION ----

        total_pages = (total_logs_count + per_page - 1) // per_page if total_logs_count > 0 else 0
        pagination_query_params = {}

    return render_template(
        'admin/activity_logs.html',
        logs=logs, # Pass the processed list
        title='Activity Logs',
        current_page=page,
        total_pages=total_pages,
        endpoint='admin.admin_activity_logs',
        query_params=pagination_query_params
    )


@bp.route('/reports/sales-over-time', methods=['GET'])
@login_required
@admin_required
def report_sales_over_time():
    # Default to last 30 days
    end_date_str = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))
    start_date_str = request.args.get('start_date', (datetime.now() - timedelta(days=29)).strftime('%Y-%m-%d'))

    try:
        # Validate and parse dates
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        
        # Ensure end_date is not before start_date
        if end_date < start_date:
            flash("End date cannot be before start date.", "warning")
            # Reset to default if invalid range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=29)
            end_date_str = end_date.strftime('%Y-%m-%d')
            start_date_str = start_date.strftime('%Y-%m-%d')

    except ValueError:
        flash("Invalid date format. Please use YYYY-MM-DD.", "warning")
        # Reset to default if parsing fails
        end_date = datetime.now()
        start_date = end_date - timedelta(days=29)
        end_date_str = end_date.strftime('%Y-%m-%d')
        start_date_str = start_date.strftime('%Y-%m-%d')

    labels = []
    data_points = []
    total_sales_period = 0
    total_orders_period = 0

    try:
        with get_db_connection() as conn:
            query = """
                SELECT 
                    DATE(order_date) as sale_day, 
                    SUM(total_amount) as daily_total,
                    COUNT(id) as daily_orders
                FROM orders
                WHERE status = 'Paid' AND DATE(order_date) BETWEEN ? AND ?
                GROUP BY sale_day
                ORDER BY sale_day ASC;
            """
            raw_sales_data = conn.execute(query, (start_date_str, end_date_str)).fetchall()

            # Create a dictionary for quick lookup of sales by day
            sales_by_day = {str(row['sale_day']): {'total': row['daily_total'], 'orders': row['daily_orders']} for row in raw_sales_data}

            # Iterate through the selected date range to ensure all days are present (even with 0 sales)
            current_date = start_date
            while current_date <= end_date:
                day_str = current_date.strftime('%Y-%m-%d')
                labels.append(current_date.strftime('%b %d')) # Format for chart label (e.g., Jan 01)
                
                if day_str in sales_by_day:
                    daily_total = sales_by_day[day_str]['total']
                    daily_orders = sales_by_day[day_str]['orders']
                    data_points.append(daily_total)
                    total_sales_period += daily_total
                    total_orders_period += daily_orders
                else:
                    data_points.append(0) # No sales for this day
                
                current_date += timedelta(days=1)
                
    except DatabaseError as e:
        current_app.logger.error(f"Database error fetching sales report: {e}")
        flash("Error fetching sales report data.", "danger")
    except Exception as e_gen:
        current_app.logger.error(f"Unexpected error fetching sales report: {e_gen}", exc_info=True)
        flash("An unexpected error occurred.", "danger")


    return render_template(
        'admin/report_sales_over_time.html',
        title='Sales Over Time Report',
        labels=json.dumps(labels), # Pass as JSON string for JavaScript
        data_points=json.dumps(data_points), # Pass as JSON string
        start_date=start_date_str,
        end_date=end_date_str,
        total_sales_period=total_sales_period,
        total_orders_period=total_orders_period,
        average_order_value=(total_sales_period / total_orders_period) if total_orders_period > 0 else 0
    )


@bp.route('/reports/sales-by-product', methods=['GET'])
@login_required
@admin_required
def report_sales_by_product():
    # Default to last 30 days
    end_date_str = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))
    start_date_str = request.args.get('start_date', (datetime.now() - timedelta(days=29)).strftime('%Y-%m-%d'))
    sort_by = request.args.get('sort_by', 'revenue')  # 'revenue' or 'quantity'
    category_filter = request.args.get('category', 'All')

    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        if end_date < start_date:
            flash("End date cannot be before start date. Using default range.", "warning")
            end_date = datetime.now()
            start_date = end_date - timedelta(days=29)
            end_date_str = end_date.strftime('%Y-%m-%d')
            start_date_str = start_date.strftime('%Y-%m-%d')
    except ValueError:
        flash("Invalid date format. Using default range.", "warning")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=29)
        end_date_str = end_date.strftime('%Y-%m-%d')
        start_date_str = start_date.strftime('%Y-%m-%d')

    products_sales_data = []
    categories = ['All']  # For category filter dropdown

    try:
        with get_db_connection() as conn:
            # Fetch categories for filter dropdown
            cat_rows = conn.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category").fetchall()
            categories.extend([row['category'] for row in cat_rows])

            base_query = """
                SELECT 
                    p.id as product_id,
                    p.name as product_name,
                    p.category as product_category,
                    SUM(oi.quantity) AS total_quantity_sold,
                    SUM(oi.price * oi.quantity) AS total_revenue_generated,
                    AVG(oi.price) AS average_selling_price 
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                JOIN orders o ON oi.order_id = o.id
            """
            conditions = ["o.status = 'Paid'", "DATE(o.order_date) BETWEEN ? AND ?"]
            params = [start_date_str, end_date_str]

            if category_filter != 'All':
                conditions.append("p.category = ?")
                params.append(category_filter)
            
            if conditions:
                base_query += " WHERE " + " AND ".join(conditions)
            
            base_query += " GROUP BY p.id, p.name, p.category"

            if sort_by == 'quantity':
                base_query += " ORDER BY total_quantity_sold DESC"
            else:  # Default sort by revenue
                base_query += " ORDER BY total_revenue_generated DESC"
            
            base_query += " LIMIT 100;"  # Limit results for performance

            products_sales_data = conn.execute(base_query, tuple(params)).fetchall()

    except DatabaseError as e:
        current_app.logger.error(f"Database error fetching sales by product report: {e}")
        flash("Error fetching sales by product report data.", "danger")
    except Exception as e_gen:
        current_app.logger.error(f"Unexpected error: {e_gen}", exc_info=True)
        flash("An unexpected error occurred.", "danger")

    return render_template(
        'admin/report_sales_by_product.html',
        title='Sales by Product Report',
        sales_data=products_sales_data,
        start_date=start_date_str,
        end_date=end_date_str,
        sort_by=sort_by,
        categories=categories,
        current_category=category_filter
    )


@bp.route('/reports/low-stock', methods=['GET'])
@login_required
@admin_required
def report_low_stock():
    threshold_str = request.args.get('threshold', str(current_app.config.get('LOW_STOCK_THRESHOLD', 5)))
    try:
        threshold = int(threshold_str)
        if threshold < 0: threshold = 5 # Default if invalid
    except ValueError:
        threshold = current_app.config.get('LOW_STOCK_THRESHOLD', 5)
        flash("Invalid threshold value. Using default.", "warning")
    
    low_stock_products = []
    try:
        with get_db_connection() as conn:
            query = """
                SELECT id, name, category, stock, price 
                FROM products 
                WHERE stock > 0 AND stock <= ? 
                ORDER BY stock ASC, name ASC;
            """
            low_stock_products = conn.execute(query, (threshold,)).fetchall()
    except DatabaseError as e:
        current_app.logger.error(f"Database error fetching low stock report: {e}")
        flash("Error fetching low stock report data.", "danger")
    except Exception as e_gen:
        current_app.logger.error(f"Unexpected error: {e_gen}", exc_info=True)
        flash("An unexpected error occurred.", "danger")

    return render_template(
        'admin/report_low_stock.html',
        title='Low Stock Report',
        products=low_stock_products,
        threshold=threshold
    )
