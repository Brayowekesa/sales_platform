"""Database connection and schema setup."""
import sqlite3
from flask import current_app


def get_db_connection():
    conn = sqlite3.connect(current_app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    if True:  # called from create_app() inside an app context
        conn = get_db_connection()
        # The 'with conn:' block handles commit/rollback
        with conn: # Line ~80 or so, depending on exact start of your file
            c = conn.cursor()
            c.executescript(''' 
               CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    confirmed INTEGER DEFAULT 0,
                    verification_code TEXT,
                    verification_code_expires DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
               );
                CREATE TABLE IF NOT EXISTS promotions (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     title TEXT NOT NULL,
                     image_path TEXT,
                     target_url TEXT,
                     alt_text TEXT,
                     original_price REAL,       
                     promo_price REAL,          
                     discount_text TEXT,       
                     is_active INTEGER DEFAULT 1,
                     display_order INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    );
                CREATE TABLE IF NOT EXISTS products (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT NOT NULL,
                     description TEXT,
                     image TEXT DEFAULT 'uploads/default.jpg',
                     category TEXT DEFAULT 'Uncategorized',
                     stock INTEGER DEFAULT 0,
                     price REAL NOT NULL,      
                     original_price REAL,      
                     sale_start_date DATETIME, 
                     sale_end_date DATETIME,   
                     is_on_sale INTEGER DEFAULT 0,
                     added_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
                CREATE TABLE IF NOT EXISTS carts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                    UNIQUE (user_id, product_id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    order_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_amount REAL NOT NULL,
                    status TEXT DEFAULT 'Pending', 
                    payment_method TEXT,          
                    shipping_address TEXT,        
                    transaction_id TEXT,          
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL 
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL, 
                    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL 
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
                    comment TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                    UNIQUE(user_id, product_id)
                );

                CREATE TABLE IF NOT EXISTS wishlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                    UNIQUE (user_id, product_id)
                );

                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER, 
                    action TEXT NOT NULL,
                    details TEXT,     -- THIS IS THE COLUMN for 'details'
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                PRAGMA journal_mode=WAL;
            ''') # End of triple-quoted string
        # Add missing verification columns for existing databases created before this update.
        columns = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
        if 'verification_code' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN verification_code TEXT')
        if 'verification_code_expires' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN verification_code_expires DATETIME')
        c.execute('''CREATE TABLE IF NOT EXISTS rate_limit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL,
                        key TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_rate_limit ON rate_limit_events(kind, key, created_at)')
        if 'verification_attempts' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN verification_attempts INTEGER DEFAULT 0')
        if 'phone' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN phone TEXT')   # remembered for the next checkout

        # --- Orders: who receives it, how it is delivered, and what delivery costs ---
        order_columns = [row[1] for row in c.execute("PRAGMA table_info(orders)").fetchall()]
        for col, ddl in [('contact_name', 'TEXT'), ('contact_phone', 'TEXT'), ('delivery_method', "TEXT"),
                         ('delivery_zone', 'TEXT'), ('delivery_fee', 'REAL DEFAULT 0'), ('subtotal', 'REAL')]:
            if col not in order_columns:
                c.execute(f'ALTER TABLE orders ADD COLUMN {col} {ddl}')

        # --- Delivery areas and pickup points (edited in Admin -> Delivery) ---
        c.execute('''CREATE TABLE IF NOT EXISTS delivery_zones (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        kind TEXT NOT NULL DEFAULT 'delivery',      -- 'delivery' or 'pickup'
                        fee REAL NOT NULL DEFAULT 0,
                        free_over REAL,                              -- free delivery when the subtotal reaches this
                        eta TEXT,                                    -- e.g. 'Same day', '1-2 days'
                        details TEXT,                                -- areas covered, or the pickup address and hours
                        is_active INTEGER DEFAULT 1,
                        sort_order INTEGER DEFAULT 0)''')
        if c.execute('SELECT COUNT(*) FROM delivery_zones').fetchone()[0] == 0:
            c.executemany('INSERT INTO delivery_zones (name, kind, fee, free_over, eta, details, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)', [
                ('Nairobi CBD', 'delivery', 150, 3000, 'Same day', 'Orders placed before 2 pm arrive the same day.', 0),
                ('Nairobi suburbs', 'delivery', 250, 3000, 'Same or next day', 'Westlands, Kilimani, Kileleshwa, South B/C, Eastlands, Kasarani and nearby.', 1),
                ('Greater Nairobi', 'delivery', 400, 5000, '1-2 days', 'Kiambu, Ruiru, Thika Road, Rongai, Kitengela, Syokimau, Athi River.', 2),
                ('Rest of Kenya (courier)', 'delivery', 600, None, '2-4 days', 'Sent by courier to your nearest town office.', 3),
                ('Pick up from our shop', 'pickup', 0, None, 'Ready within 24 hours', 'Set your shop address and opening hours in Admin -> Delivery.', 4),
            ])

        # --- Shop settings (contact details etc., edited in Admin -> Shop settings) ---
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')

        # --- Order status history (timeline on the order page) ---
        c.execute('''CREATE TABLE IF NOT EXISTS order_status_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        note TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE)''')

        # --- Extra product photos (the main photo stays in products.image) ---
        c.execute('''CREATE TABLE IF NOT EXISTS product_images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product_id INTEGER NOT NULL,
                        path TEXT NOT NULL,
                        sort_order INTEGER DEFAULT 0,
                        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE)''')

        # The upgrades above run after the 'with conn:' block, so save them explicitly
        conn.commit()
        conn.close()
        print("Database initialized (from init_db function).") # Added print statement
