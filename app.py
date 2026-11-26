import os
import uuid
from urllib.parse import urlparse
import sqlite3
import random
from functools import wraps # For decorators
from datetime import datetime, timedelta
import base64 # For M-Pesa if used, not directly for this part
from dotenv import load_dotenv

# Load environment variables from a local .env file if present.
load_dotenv()

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, session, abort, current_app # Added current_app
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# WTForms Imports - CRITICAL SECTION
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed # Using FileField from flask_wtf.file

from wtforms import (
    StringField, 
    PasswordField, 
    SubmitField, 
    BooleanField, 
    IntegerField, 
    TextAreaField,
    FloatField,
    SelectField  # Ensure SelectField is here
)
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, NumberRange # Ensure NumberRange is here
from wtforms.widgets import TextArea 

# Other Flask Extensions
from flask_bootstrap import Bootstrap
from flask_mail import Mail, Message
from flask_wtf import CSRFProtect # CSRFProtect is imported directly like this, not just FlaskForm
from itsdangerous import URLSafeTimedSerializer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

# --- Currency formatting: change CURRENCY_SYMBOL (or env var) to switch the whole site ---
CURRENCY_SYMBOL = os.environ.get('CURRENCY_SYMBOL', 'KSh')

app.jinja_env.globals['CURRENCY_SYMBOL'] = CURRENCY_SYMBOL

PLACEHOLDER_IMAGE = 'images/placeholder.svg'

def product_img(path):
    """Static URL for a product/promo image, falling back to a placeholder when the file is missing."""
    if path and os.path.isfile(os.path.join(BASE_DIR, 'static', path)):
        return url_for('static', filename=path)
    return url_for('static', filename=PLACEHOLDER_IMAGE)

app.jinja_env.globals['product_img'] = product_img


def is_safe_next_url(target):
    """Only allow redirects to paths on this site (blocks ?next=https://evil.example)."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith('/') and not target.startswith('//')


@app.template_filter('money')
def money_filter(value):
    """Format a number as currency, e.g. KSh 8,999 or KSh 1,249.50"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{CURRENCY_SYMBOL} 0"
    return f"{CURRENCY_SYMBOL} {v:,.0f}" if v == int(v) else f"{CURRENCY_SYMBOL} {v:,.2f}"


# IMPORTANT: Load sensitive data from environment variables in production
# SECRET_KEY signs sessions and password-reset tokens. Set FLASK_SECRET_KEY in production
# (generate one with: python -c "import secrets; print(secrets.token_hex(32))").
# If it is missing we generate a random key for this run only: safe, but everyone is
# logged out on restart and multiple server workers won't share sessions.
_secret_key = os.environ.get('FLASK_SECRET_KEY')
if not _secret_key:
    import secrets as _secrets
    _secret_key = _secrets.token_hex(32)
    print("WARNING: FLASK_SECRET_KEY is not set - using a temporary random key (sessions reset on restart).")
app.config['SECRET_KEY'] = _secret_key

# Session cookie hardening (set SESSION_COOKIE_SECURE=1 when serving over HTTPS)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
allowed_register_emails = os.environ.get('ALLOWED_REGISTER_EMAILS', '').strip()
app.config['ALLOWED_REGISTER_EMAILS'] = {
    email.strip().lower()
    for email in allowed_register_emails.split(',')
    if email.strip()
}

# Mail Configuration (Use environment variables for credentials)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your_email@example.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_APP_PASSWORD', 'your_email_app_password')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', os.environ.get('MAIL_USERNAME', 'your_email@example.com'))

# M-Pesa Configuration (Use environment variables)
app.config['MPESA_CONSUMER_KEY'] = os.environ.get('MPESA_CONSUMER_KEY', 'your_consumer_key')
app.config['MPESA_CONSUMER_SECRET'] = os.environ.get('MPESA_CONSUMER_SECRET', 'your_consumer_secret')
app.config['MPESA_BUSINESS_SHORT_CODE'] = os.environ.get('MPESA_BUSINESS_SHORT_CODE', '174379')
app.config['MPESA_PASSKEY'] = os.environ.get('MPESA_PASSKEY', 'your_mpesa_passkey')
app.config['MPESA_CALLBACK_URL'] = os.environ.get('MPESA_CALLBACK_URL', 'https://yourdomain.com/mpesa_callback') # Needs to be publicly accessible
# app.py - Configuration
app.config['PROMOTIONS_UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads', 'promotions')
# ...
# Ensure the promotions uploads folder exists (in the same section as the product uploads folder check)
os.makedirs(app.config['PROMOTIONS_UPLOAD_FOLDER'], exist_ok=True)

# Ensure the uploads folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize Extensions
csrf = CSRFProtect(app)
mail = Mail(app)
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info' # Optional: for styling flash messages
bootstrap = Bootstrap(app) # Initialize Bootstrap

# --- Shared category choices for products and navigation ---
PRODUCT_CATEGORY_CHOICES = [
    ('Electronics', 'Electronics'),
    ('Home & Kitchen', 'Home & Kitchen'),
    ('Fashion & Wear', 'Fashion & Wear'),
    ('Beauty & Personal Care', 'Beauty & Personal Care'),
    ('Health & Wellness', 'Health & Wellness'),
    ('Sports & Outdoors', 'Sports & Outdoors'),
    ('Office & School', 'Office & School'),
]

# --- Database Setup ---
DATABASE = os.path.join(BASE_DIR, 'sales.db')

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with app.app_context(): # Ensures we are within app context if get_db_connection needs it
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

        # No explicit conn.commit() needed here due to 'with conn:'
        print("Database initialized (from init_db function).") # Added print statement

# Ensure the required tables exist as soon as the app is imported/started.
init_db()

# --- User Model and Authentication ---
class User(UserMixin):
    def __init__(self, id, username, email, password_hash, is_admin=0, confirmed=0):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.confirmed = confirmed # ADDED

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as conn:
        user_data = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_data:
            return User(
                id=user_data['id'],
                username=user_data['username'],
                email=user_data['email'],
                password_hash=user_data['password_hash'],
                is_admin=user_data['is_admin'],
                confirmed=user_data['confirmed'] # ADDED
            )
        return None

# --- Utility Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# In app.py
def log_activity(action, user_id=None, details=None): # Modified
    # Using app.app_context() here is good practice if get_db_connection might need app config
    # or if you plan to call this from outside a request context sometimes.
    # For calls within a request, direct get_db_connection should be fine if it's simple.
    try:
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO activity_logs (user_id, action, details) VALUES (?, ?, ?)',
                (user_id, action, details)
            )
            conn.commit()
    except Exception as e:
        # Log this error to Flask logger to avoid crashing if logging itself fails
        # Make sure 'app' is defined if using app.logger. You might need current_app.logger
        # from flask import current_app
        # current_app.logger.error(f"Failed to log activity: {action}, Error: {e}")
        print(f"CRITICAL: Failed to log activity: {action}, Error: {e}") # Fallback to print

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Unauthorized access. Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# In app.py
@app.context_processor
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
            app.logger.error(f"Error fetching cart count for context processor: {e}")
            pass # Default to 0 if error
    
    # Categories for navbar
    nav_categories = [choice[0] for choice in PRODUCT_CATEGORY_CHOICES]
    try:
        with get_db_connection() as conn:
            categories_data = conn.execute(
                'SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != "" ORDER BY category LIMIT 7'
            ).fetchall()
            product_categories = [row['category'] for row in categories_data]
            if product_categories:
                nav_categories = product_categories
    except Exception as e:
         app.logger.error(f"Error fetching categories for navbar: {e}")


    # Products already in the user's wishlist (so cards can show a filled heart)
    wishlist_ids = set()
    if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
        try:
            with get_db_connection() as conn:
                wishlist_ids = {r['product_id'] for r in conn.execute(
                    'SELECT product_id FROM wishlists WHERE user_id = ?', (current_user.id,)).fetchall()}
        except Exception as e:
            app.logger.error(f"Error fetching wishlist ids: {e}")

    return dict(
        wishlist_ids=wishlist_ids,
        cart_item_count=cart_item_count,
        current_year=datetime.utcnow().year, # <<< THIS PROVIDES current_year
        SCRIPT_START_YEAR=2023, # Or your desired start year for copyright range
        categories_for_navbar=nav_categories
    )

# --- WTForms ---
class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=25)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

class LoginForm(FlaskForm):
    # Use email or username to login
    login_identifier = StringField('Username or Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')

class VerifyEmailForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    verification_code = StringField('Verification Code', validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField('Verify Account')

# app.py

# ... (other form definitions) ...
# --- User Authentication Routes ---
def generate_verification_code():
    return str(random.randint(100000, 999999))


def is_allowed_registration_email(email):
    allowed = app.config.get('ALLOWED_REGISTER_EMAILS', set())
    normalized = (email or '').strip().lower()
    if not normalized:
        return False
    if not allowed:
        return True
    return normalized in allowed


def send_confirmation_email(user_email):
    token = s.dumps(user_email, salt='email-confirm')
    confirm_url = url_for('confirm_email', token=token, _external=True)
    subject = "Confirm Your Account"
    body = f"Thank you for registering! Please click the link to confirm your email address: {confirm_url}"
    msg = Message(subject, recipients=[user_email], body=body)
    try:
        mail.send(msg)
        log_activity("Confirmation email sent", details=f"To: {user_email}")
    except Exception as e:
        log_activity("Failed to send confirmation email", details=f"To: {user_email}, Error: {str(e)}")
        app.logger.error(f"Failed to send email: {e}") # Log to Flask logger


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
        app.logger.error(f"Failed to send verification email: {e}")
        if app.debug:
            app.logger.warning(f"DEBUG MODE: verification code for {user_email} is {code}")
        return False

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        email_value = form.email.data.strip().lower()
        if not is_allowed_registration_email(email_value):
            flash('This app is configured for approved email addresses only. Please use the registered administrator email.', 'danger')
            return render_template('register.html', title='Register', form=form)

        hashed_password = generate_password_hash(form.password.data)
        code = generate_verification_code()
        expires_at = (datetime.utcnow() + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            with get_db_connection() as conn:
                conn.execute(
                    'INSERT INTO users (username, email, password_hash, confirmed, verification_code, verification_code_expires) VALUES (?, ?, ?, ?, ?, ?)',
                    (form.username.data, email_value, hashed_password, 0, code, expires_at)
                )
                conn.commit()
            sent = send_verification_email(email_value, code)
            if sent:
                flash('Registration successful! Check your email for the 6-digit verification code.', 'success')
            else:
                flash('Registration successful, but the verification email could not be delivered. Please contact support.', 'warning')
            log_activity("User registration initiated", details=f"Username: {form.username.data}")
            return redirect(url_for('verify_email', email=email_value))
        except sqlite3.IntegrityError:
            with get_db_connection() as conn:
                existing_username = conn.execute('SELECT id FROM users WHERE username = ?', (form.username.data.strip(),)).fetchone()
                existing_email = conn.execute('SELECT id FROM users WHERE email = ?', (email_value,)).fetchone()
            if existing_username and existing_email:
                flash('This username and email are already registered.', 'danger')
            elif existing_username:
                flash('This username is already taken.', 'danger')
            else:
                flash('This email is already registered.', 'danger')
        except Exception as e:
            flash(f'An error occurred: {str(e)}', 'danger')
            log_activity("User registration failed", details=f"Username: {form.username.data}, Error: {str(e)}")
    return render_template('register.html', title='Register', form=form)

@app.route('/verify_email', methods=['GET', 'POST'])
def verify_email():
    default_email = request.args.get('email', '')
    form = VerifyEmailForm(email=default_email)
    if form.validate_on_submit():
        email = form.email.data.strip()
        submitted_code = form.verification_code.data.strip()

        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            if not user:
                flash('No account found for that email.', 'danger')
                return redirect(url_for('register'))

            if user['confirmed']:
                flash('Account already confirmed. Please login.', 'info')
                return redirect(url_for('login'))

            expires_at = user['verification_code_expires']
            if not expires_at or datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S') < datetime.utcnow():
                new_code = generate_verification_code()
                new_expires_at = (datetime.utcnow() + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute(
                    'UPDATE users SET verification_code = ?, verification_code_expires = ? WHERE id = ?',
                    (new_code, new_expires_at, user['id'])
                )
                conn.commit()
                send_verification_email(email, new_code)
                flash('Your verification code expired. A new one has been sent to your email.', 'warning')
                return redirect(url_for('verify_email', email=email))

            if submitted_code != user['verification_code']:
                flash('Invalid verification code.', 'danger')
                return render_template('verify_email.html', title='Verify Email', form=form)

            conn.execute(
                'UPDATE users SET confirmed = 1, verification_code = NULL, verification_code_expires = NULL WHERE id = ?',
                (user['id'],)
            )
            conn.commit()

        flash('Your account has been verified! You can now log in.', 'success')
        log_activity("Email verified", details=f"Email: {email}")
        return redirect(url_for('login'))

    return render_template('verify_email.html', title='Verify Email', form=form)

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = s.loads(token, salt='email-confirm', max_age=3600) # 1 hour expiry
    except Exception: # Catches SignatureExpired, BadTimeSignature etc.
        flash('The confirmation link is invalid or has expired.', 'danger')
        return redirect(url_for('register'))

    with get_db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('register'))
        if user['confirmed']:
            flash('Account already confirmed. Please login.', 'info')
            return redirect(url_for('login'))
        conn.execute('UPDATE users SET confirmed = 1 WHERE email = ?', (email,))
        conn.commit()
    flash('Your account has been confirmed! You can now log in.', 'success')
    log_activity("Email confirmed", details=f"Email: {email}")
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        login_identifier = form.login_identifier.data
        password = form.password.data
        with get_db_connection() as conn:
            # Check if login_identifier is an email or username
            if '@' in login_identifier:
                user_data = conn.execute("SELECT * FROM users WHERE email = ?", (login_identifier,)).fetchone()
            else:
                user_data = conn.execute("SELECT * FROM users WHERE username = ?", (login_identifier,)).fetchone()

            if user_data:
                user_obj = User(
                    id=user_data['id'], username=user_data['username'], email=user_data['email'],
                    password_hash=user_data['password_hash'], is_admin=user_data['is_admin'],
                    confirmed=user_data['confirmed']
                )
                if user_obj.verify_password(password):
                    if user_obj.confirmed:
                        login_user(user_obj, remember=form.remember.data)
                        log_activity('Logged in', user_id=user_obj.id)
                        flash('Logged in successfully.', 'success')
                        next_page = request.args.get('next')
                        if not is_safe_next_url(next_page):
                            next_page = None
                        return redirect(next_page or url_for('index'))
                    else:
                        flash('Please verify your email with the 6-digit code before logging in.', 'warning')
                        return redirect(url_for('verify_email', email=user_obj.email))
                else:
                    flash('Invalid username/email or password.', 'danger')
            else:
                flash('Invalid username/email or password.', 'danger')
    return render_template('login.html', title='Login', form=form)

@app.route('/logout', methods=['POST']) # CORRECTED: Use POST for logout
@login_required
def logout():
    log_activity('Logged out', user_id=current_user.id)
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

def send_password_reset_email(user_email):
    token = s.dumps(user_email, salt='password-reset')
    reset_url = url_for('reset_token', token=token, _external=True)
    subject = "Password Reset Request"
    body = f"To reset your password, visit the following link: {reset_url}\nIf you did not make this request then simply ignore this email."
    msg = Message(subject, recipients=[user_email], body=body)
    try:
        mail.send(msg)
        log_activity("Password reset email sent", details=f"To: {user_email}")
    except Exception as e:
        log_activity("Failed to send password reset email", details=f"To: {user_email}, Error: {str(e)}")
        app.logger.error(f"Failed to send password reset email: {e}")

@app.route("/reset_password", methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RequestResetForm()
    if form.validate_on_submit():
        with get_db_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (form.email.data,)).fetchone()
        if user:
            send_password_reset_email(user['email'])
            flash('An email has been sent with instructions to reset your password.', 'info')
        else:
            # Generic message to avoid confirming if an email exists
            flash('If an account with that email exists, a reset link has been sent.', 'info')
        return redirect(url_for('login'))
    return render_template('reset_request.html', title='Reset Password', form=form)

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    try:
        email = s.loads(token, salt='password-reset', max_age=3600) # 1 hour expiry
    except:
        flash('That is an invalid or expired token', 'warning')
        return redirect(url_for('reset_request'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        hashed_password = generate_password_hash(form.password.data)
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (hashed_password, email))
            conn.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        log_activity("Password reset successfully", details=f"Email: {email}")
        return redirect(url_for('login'))
    return render_template('reset_token.html', title='Reset Password', form=form)

# In app.py (ensure this is after app = Flask(__name__))

# ... (other app configurations and routes) ...

@app.errorhandler(404)
def not_found_error(error):
    # You can log the 404 error here if desired, e.g., log which path was not found
    # log_activity("404 Not Found", details=f"Path: {request.path}") # If you want to log 404s
    return render_template('errors/404.html', title="Page Not Found"), 404

@app.errorhandler(500)
def internal_error(error):
    # It's good practice to rollback the DB session in case the error was DB related
    # This depends on how you manage your DB sessions. If using 'with get_db_connection() as conn:'
    # extensively and the error happened outside such a block, an explicit rollback isn't always
    # automatically handled by just being in an errorhandler. However, often the request
    # context teardown might handle it. For robustness with SQLite and manual connection patterns:
    # try:
    #     db = getattr(g, '_database', None)
    #     if db is not None:
    #         db.rollback()
    # except Exception as e:
    #     app.logger.error(f"Error during rollback in 500 handler: {e}")

    log_activity("500 Internal Server Error", details=str(error)) # Log the error
    app.logger.error(f"Server Error: {error}", exc_info=True) # Log with traceback
    return render_template('errors/500.html', title="Server Error"), 500


# Continue from Part 1 of app.py ...

# --- Additional WTForms ---
class ReviewForm(FlaskForm):
    rating = StringField('Rating', validators=[DataRequired()]) # Consider using SelectField for 1-5 stars
    comment = StringField('Comment', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Submit Review')

class UpdateCartForm(FlaskForm): # For updating quantity in cart
    quantity = StringField('Quantity', validators=[DataRequired()]) # Should be IntegerField from wtforms.fields.html5
    submit = SubmitField('Update')

# --- Frontend Routes (Products, Cart, Wishlist, Reviews) ---

# app.py
# Ensure these imports are at the top of your app.py file
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
from datetime import datetime
# ... other imports ...

# (Your Flask app initialization and configuration)
# app = Flask(__name__)
# ... (app.config settings) ...
# ... (database setup, get_db_connection) ...
# ... (LoginManager setup) ...
# ... (WTForms definitions) ...
# ... (Utility functions, decorators) ...
# ... (Context processors) ...

# Ensure these imports are at the top of your app.py file
from flask import render_template, request, url_for # Add other necessary Flask imports
from flask_login import login_required # If you want the page to require login
from datetime import datetime
# Assuming get_db_connection and app (or current_app for logger) are defined elsewhere in your app.py

@app.route('/')
@login_required # Keep this if you want the homepage to require login
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
                        # Ensure app.logger is correctly accessed (e.g., from current_app or app instance)
                        # For example, using current_app:
                        # from flask import current_app
                        # current_app.logger.warning(...)
                        # Or if 'app' is your Flask instance:
                        # app.logger.warning(...)
                        # For now, using print as a fallback if logger isn't set up in this snippet's context
                        print(f"Warning: Could not parse added_at string: {prod_dict['added_at']} for product ID {prod_dict['id']} on index page")
                products.append(prod_dict)
            # --- End Main Product Fetching Logic ---

            # --- NEW: Fetch products for the "Featured/Horizontal Scroll" section ---
            # Example: Fetch first 8 most recent products.
            # Customize this query (e.g., WHERE is_featured = 1) as needed.
            featured_products_raw = conn.execute(
                'SELECT * FROM products ORDER BY added_at DESC LIMIT 8' # Adjust LIMIT as desired
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
        title='Home' if category_filter == 'All' else category_filter,
        endpoint='index',                       # For pagination
        query_params=pagination_query_params,   # For pagination
        promotions=active_promotions            # For the carousel
    )
# ... (rest of your app.py routes and CLI commands) ...

# if __name__ == '__main__':
#    app.run(debug=True, host='0.0.0.0')
# app.py
# Ensure 'datetime' is imported from the 'datetime' module at the top of your file:
# from datetime import datetime

@app.route('/product/<int:product_id>')
def product_details(product_id):
    review_form = ReviewForm()
    product_data_for_template = None # Initialize
    processed_reviews = []       # Initialize

    try:
        with get_db_connection() as conn:
            product_raw = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            if not product_raw:
                flash('Product not found.', 'danger')
                return redirect(url_for('index'))
            
            # Process product data (e.g., if it also has dates that need parsing for this page)
            product_data_for_template = dict(product_raw) # Convert to dict
            # Example: If product also had an 'added_at' to display and parse:
            # if product_data_for_template.get('added_at') and isinstance(product_data_for_template['added_at'], str):
            #     try:
            #         product_data_for_template['added_at'] = datetime.strptime(product_data_for_template['added_at'], '%Y-%m-%d %H:%M:%S')
            #     except ValueError:
            #         app.logger.warning(f"Could not parse added_at for product {product_id} in product_details")


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
                        # app.logger.warning(f"Could not parse created_at string for review: {review_dict['created_at']}")
                        print(f"Warning: Could not parse created_at string for review: {review_dict['created_at']}") # Fallback
                        # review_dict['created_at'] = None # Or keep as string and let template handle N/A
                processed_reviews.append(review_dict)
            # ---- END MODIFICATION for reviews ----
            
    except sqlite3.Error as e:
        flash("A database error occurred while fetching product details.", "danger")
        # Log the error: app.logger.error(f"DB error in product_details for {product_id}: {e}")
        print(f"DB error in product_details for {product_id}: {e}") # Fallback
        return redirect(url_for('index')) # Or an error page

    if not product_data_for_template: # Should have been caught by 'if not product_raw' but as a safeguard
        flash('Product data could not be loaded.', 'danger')
        return redirect(url_for('index'))

    return render_template(
        'product_details.html',
        product=product_data_for_template,  # Pass the (potentially processed) product dict
        reviews=processed_reviews,          # Pass the list of processed review dicts
        review_form=review_form,
        title=product_data_for_template['name']
    )
@app.route('/product/<int:product_id>/review', methods=['POST'])
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
                return redirect(url_for('product_details', product_id=product_id))

            with get_db_connection() as conn:
                # Optional: Check if user purchased the product
                # purchased = conn.execute('''
                #     SELECT 1 FROM order_items oi
                #     JOIN orders o ON oi.order_id = o.id
                #     WHERE o.user_id = ? AND oi.product_id = ?
                # ''', (current_user.id, product_id)).fetchone()
                # if not purchased:
                #     flash('You must purchase this product to review it.', 'warning')
                #     return redirect(url_for('product_details', product_id=product_id))

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
            flash(f'Error submitting review: {str(e)}', 'danger')
            log_activity("Review submission failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    else:
        # Collect form errors if any and flash them
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')
    return redirect(url_for('product_details', product_id=product_id))


@app.route('/search')
@login_required
def search():
    query = request.args.get('query', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10 # Number of search results per page

    if not query:
        # If query is empty, maybe redirect to index or show a message directly
        flash("Please enter a search term.", "info")
        return render_template('search_results.html', query=query, results=[], title='Search', total_results=0, total_pages=0, endpoint='search', query_params={'query': query})


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
        log_activity("Search performed", user_id=current_user.id, details=f"Query: {query}")

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
        endpoint='search',                       # <-- ADDED for pagination
        query_params=pagination_query_params    # <-- ADDED for pagination
    )

# --- Cart Routes ---
@app.route('/cart')
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

@app.route('/cart/add/<int:product_id>', methods=['POST'])
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
                DO UPDATE SET quantity = MIN(carts.quantity + excluded.quantity, (SELECT stock FROM products WHERE id = excluded.product_id))
            ''', (current_user.id, product_id, quantity))
            conn.commit()

            new_count_result = conn.execute(
                'SELECT SUM(quantity) as total FROM carts WHERE user_id = ?', (current_user.id,)
            ).fetchone()
            new_count = new_count_result['total'] if new_count_result and new_count_result['total'] else 0

        log_activity("Added to cart", user_id=current_user.id, details=f"Product ID: {product_id}, Quantity: {quantity}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': 'Item added to cart!', 'count': new_count})
        flash('Item added to cart!', 'success')
        return redirect(request.referrer or url_for('index'))

    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Error adding item. Product may not exist or conflict.'}), 400
    except Exception as e:
        log_activity("Add to cart failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
        app.logger.error(f"Error adding to cart: {e}")
        return jsonify({'success': False, 'message': f'An error occurred: {str(e)}'}), 500


@app.route('/cart/remove/<int:product_id>', methods=['POST'])
@login_required
def remove_from_cart(product_id):
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM carts WHERE user_id = ? AND product_id = ?', (current_user.id, product_id))
            conn.commit()
        flash('Item removed from cart.', 'success')
        log_activity("Removed from cart", user_id=current_user.id, details=f"Product ID: {product_id}")
    except Exception as e:
        flash(f'Error removing item: {str(e)}', 'danger')
        log_activity("Remove from cart failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(url_for('view_cart'))

@app.route('/cart/update/<int:product_id>', methods=['POST'])
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
                    return redirect(url_for('view_cart'))
                if product['stock'] <= 0:
                    flash(f"{product['name']} is currently out of stock. Cart not updated.", 'warning')
                    return redirect(url_for('view_cart'))
                if product['stock'] < new_quantity:
                    flash(f"Not enough stock for {product['name']}. Only {product['stock']} available. Cart not updated.", 'warning')
                    return redirect(url_for('view_cart'))

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
            flash(f'Error updating cart: {str(e)}', 'danger')
            log_activity("Cart update failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    else:
        flash('Invalid form submission for cart update.', 'danger')
    return redirect(url_for('view_cart'))


@app.route('/cart/empty', methods=['POST'])
@login_required
def empty_cart():
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM carts WHERE user_id = ?', (current_user.id,))
            conn.commit()
        flash('Cart emptied successfully.', 'success')
        log_activity("Cart emptied", user_id=current_user.id)
    except Exception as e:
        flash(f'Error emptying cart: {str(e)}', 'danger')
        log_activity("Cart empty failed", user_id=current_user.id, details=str(e))
    return redirect(url_for('view_cart'))

@app.route('/cart/count', methods=['GET']) # Primarily for AJAX
@login_required
def cart_count_api(): # Renamed from original to avoid conflict if you had a template variable named cart_count
    with get_db_connection() as conn:
        result = conn.execute('SELECT SUM(quantity) as total FROM carts WHERE user_id = ?', (current_user.id,)).fetchone()
    count = result['total'] if result and result['total'] else 0
    return jsonify({'count': count})


# --- Wishlist Routes ---
@app.route('/wishlist')
@login_required
def view_wishlist():
    with get_db_connection() as conn:
        wishlist_items = conn.execute('''
            SELECT p.id, p.name, p.price, p.image, p.stock
            FROM wishlists w
            JOIN products p ON w.product_id = p.id
            WHERE w.user_id = ?
        ''', (current_user.id,)).fetchall()
    return render_template('wishlist.html', wishlist_items=wishlist_items, title='Your Wishlist')

@app.route('/wishlist/add/<int:product_id>', methods=['POST'])
@login_required
def add_to_wishlist(product_id):
    try:
        with get_db_connection() as conn:
            # Check if product exists
            product = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
            if not product:
                flash('Product not found.', 'danger')
                return redirect(request.referrer or url_for('index'))

            conn.execute('INSERT INTO wishlists (user_id, product_id) VALUES (?, ?)', (current_user.id, product_id))
            conn.commit()
        flash('Item added to wishlist!', 'success')
        log_activity("Added to wishlist", user_id=current_user.id, details=f"Product ID: {product_id}")
    except sqlite3.IntegrityError:
        flash('Item is already in your wishlist.', 'info')
    except Exception as e:
        flash(f'Error adding to wishlist: {str(e)}', 'danger')
        log_activity("Add to wishlist failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(request.referrer or url_for('view_wishlist'))

@app.route('/wishlist/remove/<int:product_id>', methods=['POST'])
@login_required
def remove_from_wishlist(product_id):
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM wishlists WHERE user_id = ? AND product_id = ?', (current_user.id, product_id))
            conn.commit()
        flash('Item removed from wishlist.', 'success')
        log_activity("Removed from wishlist", user_id=current_user.id, details=f"Product ID: {product_id}")
    except Exception as e:
        flash(f'Error removing item from wishlist: {str(e)}', 'danger')
        log_activity("Remove from wishlist failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(url_for('view_wishlist'))

# Continue from Part 2 of app.py ...
import requests # For M-Pesa, ensure 'requests' is installed: pip install requests

# --- Additional WTForms for Admin and Checkout ---

# app.py
# Ensure FloatField is imported: from wtforms.fields import FloatField (or from wtforms import FloatField)

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(max=100)])
    price = FloatField('Current Price (Sale or Regular)', validators=[DataRequired(), NumberRange(min=0.01)]) # This is the actual price
    original_price = FloatField('Original Price (Set if Current Price is a Sale Price, otherwise leave blank or same as Current Price)', 
    validators=[Optional(), NumberRange(min=0)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=1000)]) # Changed from StringField with widget
    category = SelectField('Category', choices=PRODUCT_CATEGORY_CHOICES, validators=[DataRequired()])
    stock = IntegerField('Stock Quantity', validators=[DataRequired(), NumberRange(min=0)]) # Changed from StringField
    image = FileField('Product Image', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'])])
    submit = SubmitField('Save Product')

# ... (rest of your app.py)

class AdminUserForm(FlaskForm): # For Admin Add/Edit User
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=25)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password (leave blank to keep current)', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[EqualTo('password')])
    is_admin = BooleanField('Is Admin?')
    confirmed = BooleanField('Email Confirmed?') # Allow admin to manually confirm
    submit = SubmitField('Save User')

class AdminResetPasswordForm(FlaskForm):
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Reset Password')

class CheckoutForm(FlaskForm):
    # Basic fields, expand as needed (e.g., separate address fields)
    shipping_address = StringField('Shipping Address', validators=[DataRequired(), Length(max=200)])
    payment_method = StringField('Payment Method', validators=[DataRequired()]) # Consider RadioField with choices
    # For M-Pesa
    phone_number = StringField('M-Pesa Phone Number (e.g., 2547XXXXXXXX)', validators=[Optional(), Length(min=10, max=12)])
    submit = SubmitField('Place Order')


# --- Admin Routes ---
# --- Admin Routes (Continued from app.py - Part 3) ---
# Ensure these imports are at the top of your app.py if not already there:
# from flask import request, flash, redirect, url_for, render_template
# from werkzeug.utils import secure_filename # for product image uploads
# from werkzeug.security import generate_password_hash # for adding/resetting user passwords
# import os # for file paths
# import sqlite3 # for IntegrityError on delete product

# ProductForm, AdminUserForm, AdminResetPasswordForm should be defined before these routes

from datetime import datetime # Make sure this is at the top of your app.py

# app.py
# Ensure these are imported at the top:
# from flask import render_template, current_app (if using current_app.logger)
# from flask_login import login_required
# from datetime import datetime
# from .utils import get_db_connection, admin_required # Assuming these are defined

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    with get_db_connection() as conn:
        # Total Sales (Paid)
        total_sales_data = conn.execute('SELECT SUM(total_amount) FROM orders WHERE status = "Paid"').fetchone()
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
            JOIN orders o ON oi.order_id = o.id WHERE o.status = "Paid"
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
        processed_recent_activities=processed_recent_activities # <-- PASS NEW DATA
    )

@app.route('/admin/products')
@login_required
@admin_required
def admin_products():
    page = request.args.get('page', 1, type=int)
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
            product_dict = dict(row) # Convert sqlite3.Row to a mutable dictionary
            if product_dict.get('added_at') and isinstance(product_dict['added_at'], str):
                try:
                    # Attempt to parse the datetime string. Adjust format if necessary.
                    # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                    # If it includes milliseconds: '%Y-%m-%d %H:%M:%S.%f'
                    product_dict['added_at'] = datetime.strptime(product_dict['added_at'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    # Handle cases where parsing might fail, e.g., log it or keep as string
                    app.logger.warning(f"Could not parse added_at string: {product_dict['added_at']} for product ID: {product_dict['id']}")
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
        endpoint='admin_products',
        query_params=pagination_query_params
    )
# app.py (ensure all necessary imports are at the top)
# from flask import render_template, request, redirect, url_for, flash, current_app
# from flask_login import login_required, current_user
# from werkzeug.utils import secure_filename
# import os
# import sqlite3
# from datetime import datetime
# from .forms import ProductForm # Assuming forms.py or ProductForm is defined in this file
# from .utils import get_db_connection, allowed_file, log_activity, admin_required # Assuming utils.py

# Ensure ProductForm is defined like this:
# class ProductForm(FlaskForm):
#     name = StringField('Product Name', validators=[DataRequired(), Length(max=100)])
#     price = FloatField('Current Price (Sale or Regular)', validators=[DataRequired(), NumberRange(min=0.01)])
#     original_price = FloatField('Original Price (Set if Current Price is a Sale Price)', validators=[Optional(), NumberRange(min=0)])
#     description = TextAreaField('Description', validators=[Optional(), Length(max=1000)])
#     category = StringField('Category', validators=[DataRequired(), Length(max=50)])
#     stock = IntegerField('Stock Quantity', validators=[DataRequired(), NumberRange(min=0)])
#     image = FileField('Product Image', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
#     submit = SubmitField('Save Product')


@app.route('/admin/products/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_product():
    form = ProductForm()
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
                elif allowed_file(image_file.filename):
                    filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                    image_file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    image_filename = f"uploads/{filename}"
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
                conn.execute('''
                    INSERT INTO products (name, price, original_price, description, category, stock, image, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (name, current_price_val, db_original_price, description, category, stock_val, image_filename))
                conn.commit()
            flash('Product added successfully!', 'success')
            log_activity("Admin added product", user_id=current_user.id, details=f"Product Name: {name}")
            return redirect(url_for('admin_products'))
        except ValueError as ve: # Should be less common with typed form fields
            flash(f'Invalid data format: {str(ve)}', 'danger')
        except Exception as e:
            current_app.logger.error(f"Error adding product: {e}", exc_info=True)
            flash(f'Error adding product: {str(e)}', 'danger')
            log_activity("Admin add product failed", user_id=current_user.id, details=str(e))
            
    return render_template('admin/add_edit_product.html', form=form, title='Add Product', action='Add')


@app.route('/admin/products/edit/<int:product_id>', methods=['GET', 'POST'])
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
        return redirect(url_for('admin_products'))

    form = ProductForm() # Create empty form first

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
                if allowed_file(image_file.filename):
                    # Optional: Delete old image file if it's not default.jpg and different
                    if product['image'] and product['image'] != 'uploads/default.jpg':
                        old_image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], os.path.basename(product['image']))
                        if os.path.exists(old_image_path):
                            try:
                                os.remove(old_image_path)
                            except OSError as ose:
                                current_app.logger.error(f"Error deleting old image {old_image_path}: {ose}")
                    
                    filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                    image_file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    image_filename = f"uploads/{filename}"
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
                conn.commit()
            flash('Product updated successfully!', 'success')
            log_activity("Admin edited product", user_id=current_user.id, details=f"Product ID: {product_id}")
            return redirect(url_for('admin_products'))
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

    return render_template('admin/add_edit_product.html', form=form, title='Edit Product', product=product, action='Edit')

@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
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
                return redirect(url_for('admin_products'))
            product_data = conn.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
            if product_data:
                # Delete associated image file if not default
                if product_data['image'] and product_data['image'] != 'uploads/default.jpg':
                    image_filename = product_data['image'].split('/')[-1] # Get filename part
                    image_file_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                    if os.path.exists(image_file_path):
                        try:
                            os.remove(image_file_path)
                        except OSError as e:
                            app.logger.error(f"Error deleting image file {image_file_path}: {e}")
                            flash(f"Could not delete image file: {e}", "warning")
            
            # The 'ON DELETE CASCADE' or 'ON DELETE SET NULL' in your DB schema 
            # should handle carts, reviews, wishlists. Order_items might be set to SET NULL.
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        flash('Product deleted successfully.', 'success')
        log_activity("Admin deleted product", user_id=current_user.id, details=f"Product ID: {product_id}")
    except sqlite3.IntegrityError as e: 
        app.logger.error(f"Integrity error deleting product {product_id}: {e}")
        flash(f'Error deleting product. It might be referenced in existing orders that prevent deletion. ({str(e)})', 'danger')
    except Exception as e:
        app.logger.error(f"Error deleting product {product_id}: {e}")
        flash(f'Error deleting product: {str(e)}', 'danger')
        log_activity("Admin delete product failed", user_id=current_user.id, details=f"Product ID: {product_id}, Error: {str(e)}")
    return redirect(url_for('admin_products'))


# c:\Users\TECHLAB INNOVATIONS\my_flask_app\sales_platform\app.py
# Make sure datetime is imported: from datetime import datetime

@app.route('/admin/members')
@login_required
@admin_required
def admin_members():
    page = request.args.get('page', 1, type=int)
    per_page = 10

    with get_db_connection() as conn:
        total_members_data = conn.execute('SELECT COUNT(id) FROM users').fetchone()
        total_members_count = total_members_data[0] if total_members_data else 0

        offset = (page - 1) * per_page
        raw_members = conn.execute(
            'SELECT id, username, email, is_admin, confirmed, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (per_page, offset)
        ).fetchall()

        # ---- START MODIFICATION ----
        members = []
        for row in raw_members:
            member_dict = dict(row) # Convert sqlite3.Row to a mutable dictionary
            if member_dict.get('created_at') and isinstance(member_dict['created_at'], str):
                try:
                    # Attempt to parse the datetime string. Adjust format if necessary.
                    # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                    member_dict['created_at'] = datetime.strptime(member_dict['created_at'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    app.logger.warning(f"Could not parse created_at string: {member_dict['created_at']} for user ID: {member_dict['id']}")
                    # member_dict['created_at'] = None # Or keep as string
            members.append(member_dict)
        # ---- END MODIFICATION ----

        total_pages = (total_members_count + per_page - 1) // per_page if total_members_count > 0 else 0
        pagination_query_params = {}

    return render_template(
        'admin/members.html',
        members=members, # Pass the processed list
        title='Manage Members',
        current_page=page,
        total_pages=total_pages,
        endpoint='admin_members',
        query_params=pagination_query_params
    )
@app.route('/admin/members/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_member():
    form = AdminUserForm() # AdminUserForm from app.py (Part 3)
    if form.validate_on_submit():
        if not form.password.data: # Password is required for new user
            form.password.errors.append("Password is required for new users.") # Attach error to form field
        else:
            try:
                hashed_password = generate_password_hash(form.password.data)
                with get_db_connection() as conn:
                    conn.execute('''
                        INSERT INTO users (username, email, password_hash, is_admin, confirmed, created_at)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ''', (form.username.data, form.email.data, hashed_password,
                          1 if form.is_admin.data else 0, 
                          1 if form.confirmed.data else 0))
                    conn.commit()
                flash('Member added successfully!', 'success')
                log_activity("Admin added member", user_id=current_user.id, details=f"Username: {form.username.data}")
                return redirect(url_for('admin_members'))
            except sqlite3.IntegrityError:
                flash('Username or email already exists.', 'danger')
            except Exception as e:
                app.logger.error(f"Error adding member: {e}")
                flash(f'Error adding member: {str(e)}', 'danger')
                log_activity("Admin add member failed", user_id=current_user.id, details=f"Error: {str(e)}")
    return render_template('admin/add_edit_member.html', form=form, title='Add Member', action='Add')

@app.route('/admin/members/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_member(user_id):
    with get_db_connection() as conn:
        member = conn.execute('SELECT id, username, email, is_admin, confirmed FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not member:
        flash('Member not found.', 'danger')
        return redirect(url_for('admin_members'))

    form = AdminUserForm(obj=member) # Pre-populate
    if form.validate_on_submit():
        try:
            with get_db_connection() as conn:
                if form.password.data: # If new password is provided
                    hashed_password = generate_password_hash(form.password.data)
                    conn.execute('''
                        UPDATE users SET username = ?, email = ?, password_hash = ?, is_admin = ?, confirmed = ?
                        WHERE id = ?
                    ''', (form.username.data, form.email.data, hashed_password,
                          1 if form.is_admin.data else 0, 
                          1 if form.confirmed.data else 0, 
                          user_id))
                else: # Keep old password
                    conn.execute('''
                        UPDATE users SET username = ?, email = ?, is_admin = ?, confirmed = ?
                        WHERE id = ?
                    ''', (form.username.data, form.email.data,
                          1 if form.is_admin.data else 0, 
                          1 if form.confirmed.data else 0, 
                          user_id))
                conn.commit()
            flash('Member updated successfully!', 'success')
            log_activity("Admin edited member", user_id=current_user.id, details=f"User ID: {user_id}")
            return redirect(url_for('admin_members'))
        except sqlite3.IntegrityError:
            flash('Username or email already exists for another user.', 'danger')
        except Exception as e:
            app.logger.error(f"Error updating member {user_id}: {e}")
            flash(f'Error updating member: {str(e)}', 'danger')
            log_activity("Admin edit member failed", user_id=current_user.id, details=f"User ID: {user_id}, Error: {str(e)}")
    elif request.method == 'GET':
        # For GET, clear password fields from pre-population as they are not the actual passwords
        form.password.data = ""
        form.confirm_password.data = ""
        # is_admin and confirmed flags should be correctly set by obj=member
        form.is_admin.data = bool(member['is_admin'])
        form.confirmed.data = bool(member['confirmed'])
            
    return render_template('admin/add_edit_member.html', form=form, title='Edit Member', member=member, action='Edit')


@app.route('/admin/members/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_member(user_id):
    if user_id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin_members'))
    try:
        with get_db_connection() as conn:
            # Orders must keep their customer, so a member with orders can't be deleted
            has_orders = conn.execute("SELECT 1 FROM orders WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
            if has_orders:
                flash('This member has orders and cannot be deleted. Remove their admin rights or reset their password instead.', 'warning')
                return redirect(url_for('admin_members'))
            # Database schema's ON DELETE constraints should handle related data
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        flash('Member deleted successfully.', 'success')
        log_activity("Admin deleted member", user_id=current_user.id, details=f"Deleted User ID: {user_id}")
    except Exception as e:
        app.logger.error(f"Error deleting member {user_id}: {e}")
        flash(f'Error deleting member: {str(e)}', 'danger')
        log_activity("Admin delete member failed", user_id=current_user.id, details=f"Deleted User ID: {user_id}, Error: {str(e)}")
    return redirect(url_for('admin_members'))

@app.route('/admin/members/reset-password/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_reset_user_password(user_id): # Renamed from original if it was admin_reset_password
    form = AdminResetPasswordForm() # AdminResetPasswordForm from app.py (Part 3)
    with get_db_connection() as conn:
        user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('admin_members'))

    if form.validate_on_submit():
        new_password_hash = generate_password_hash(form.new_password.data)
        try:
            with get_db_connection() as conn:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
                conn.commit()
            flash(f"Password for {user['username']} has been reset.", "success")
            log_activity("Admin reset user password", user_id=current_user.id, details=f"Reset password for User ID: {user_id}")
            return redirect(url_for('admin_edit_member', user_id=user_id)) # Or back to admin_members
        except Exception as e:
            app.logger.error(f"Error resetting password for user {user_id}: {e}")
            flash(f"Error resetting password: {str(e)}", "danger")
            log_activity("Admin reset user password failed", user_id=current_user.id, details=f"User ID: {user_id}, Error: {str(e)}")
    return render_template('admin/reset_user_password.html', form=form, user=user, title=f"Reset Password for {user['username']}")


# c:\Users\TECHLAB INNOVATIONS\my_flask_app\sales_platform\app.py
# Make sure datetime is imported: from datetime import datetime

# app.py (ensure all necessary imports are at the top)
from flask import render_template, request, url_for, current_app, flash # Added flash
@app.route('/admin/orders')
@login_required
@admin_required
def admin_orders():
    page = request.args.get('page', 1, type=int)
    per_page = 15 # Number of orders per page
    status_filter = request.args.get('status', None)

    # --- Start Debug Prints ---
    print(f"\n--- admin_orders route called ---")
    print(f"Request arguments: {request.args}")
    print(f"Parsed status_filter: '{status_filter}'")
    print(f"Parsed page: {page}")
    # --- End Debug Prints ---

    # Initialize lists and counts
    orders = []
    total_orders_count = 0
    total_pages = 0
    distinct_statuses = ['All'] # Default with 'All'
    pagination_query_params = {}

    try:
        with get_db_connection() as conn:
            # Base SQL queries
            base_count_sql = 'SELECT COUNT(o.id) FROM orders o JOIN users u ON o.user_id = u.id'
            base_select_sql = '''SELECT o.id, u.username, o.order_date, o.total_amount, o.status, o.payment_method 
                                 FROM orders o JOIN users u ON o.user_id = u.id'''

            sql_filter_params = [] # Parameters for the SQL WHERE clause

            # Filtering logic
            where_conditions = []
            if status_filter and status_filter != 'All':
                where_conditions.append('o.status = ?')
                sql_filter_params.append(status_filter)
                pagination_query_params['status'] = status_filter # For pagination links

            where_clause_str = ""
            if where_conditions:
                where_clause_str = ' WHERE ' + ' AND '.join(where_conditions)

            # Construct final SQL queries
            final_count_sql = base_count_sql + where_clause_str
            final_select_sql = base_select_sql + where_clause_str + ' ORDER BY o.order_date DESC LIMIT ? OFFSET ?'

            # --- Debug Prints for SQL ---
            print(f"SQL filter params: {sql_filter_params}")
            print(f"Final Count SQL: {final_count_sql}")
            print(f"Final Select SQL: {final_select_sql}")
            # --- End Debug Prints for SQL ---

            # Execute count query
            total_orders_data = conn.execute(final_count_sql, tuple(sql_filter_params)).fetchone()
            total_orders_count = total_orders_data[0] if total_orders_data and total_orders_data[0] is not None else 0
            
            print(f"Total orders COUNT with filter: {total_orders_count}") # DEBUG

            # Calculate offset for pagination
            offset = (page - 1) * per_page
            
            # Parameters for the main select query (filter params + pagination params)
            select_query_full_params = tuple(sql_filter_params + [per_page, offset])
            print(f"Full params for Select SQL (filter + pagination): {select_query_full_params}") # DEBUG

            # Execute select query for orders
            raw_orders = conn.execute(final_select_sql, select_query_full_params).fetchall()
            print(f"Number of raw_orders FETCHED: {len(raw_orders)}") # DEBUG
            # if raw_orders: # DEBUG - Print first fetched order if any
            #     print(f"First raw_order content: {dict(raw_orders[0])}")

            for row in raw_orders:
                order_dict = dict(row)
                if order_dict.get('order_date') and isinstance(order_dict['order_date'], str):
                    try:
                        order_dict['order_date'] = datetime.strptime(order_dict['order_date'], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        log_msg = f"Could not parse order_date string: {order_dict['order_date']} for order ID: {order_dict['id']}"
                        if hasattr(current_app, 'logger'): current_app.logger.warning(log_msg)
                        else: print(f"WARNING: {log_msg}")
                orders.append(order_dict)
            
            total_pages = (total_orders_count + per_page - 1) // per_page if total_orders_count > 0 else 0
            
            # Get distinct statuses for the filter dropdown
            distinct_statuses_rows = conn.execute('SELECT DISTINCT status FROM orders WHERE status IS NOT NULL ORDER BY status').fetchall()
            distinct_statuses.extend([row['status'] for row in distinct_statuses_rows])

    except sqlite3.Error as e:
        error_msg = f"Database error in admin_orders: {e}"
        if hasattr(current_app, 'logger'): current_app.logger.error(error_msg)
        else: print(f"ERROR: {error_msg}")
        flash("A database error occurred while retrieving orders.", "danger")
        # Fallback to empty lists so template doesn't break
        orders = []
        total_orders_count = 0
        total_pages = 0
        # distinct_statuses will have 'All'
    except Exception as e: # Catch any other unexpected errors
        error_msg = f"An unexpected error occurred in admin_orders: {e}"
        if hasattr(current_app, 'logger'): current_app.logger.error(error_msg, exc_info=True) # Log with traceback
        else: print(f"ERROR: {error_msg}")
        flash("An unexpected error occurred. Please try again.", "danger")
        orders = []
        total_orders_count = 0
        total_pages = 0

    print(f"Rendering template with {len(orders)} orders, total_pages: {total_pages}") # DEBUG
    print(f"--- admin_orders route finished ---\n") # DEBUG

    return render_template(
        'admin/orders.html',
        orders=orders,
        title='Manage Orders',
        current_page=page,
        total_pages=total_pages,
        endpoint='admin_orders',
        query_params=pagination_query_params, # Used to build pagination links correctly with filters
        distinct_statuses=list(dict.fromkeys(distinct_statuses)), # Ensure 'All' is first and statuses are unique
        current_status_filter=status_filter if status_filter else 'All'
    )
@app.route('/admin/orders/<int:order_id>')
@login_required
@admin_required
def admin_order_details(order_id):
    with get_db_connection() as conn:
        raw_order = conn.execute('''
            SELECT o.*, u.username, u.email
            FROM orders o JOIN users u ON o.user_id = u.id
            WHERE o.id = ?
        ''', (order_id,)).fetchone()

        if not raw_order:
            flash('Order not found.', 'danger')
            return redirect(url_for('admin_orders'))

        # ---- START MODIFICATION ----
        order = dict(raw_order) # Convert sqlite3.Row to a mutable dictionary
        if order.get('order_date') and isinstance(order['order_date'], str):
            try:
                # Attempt to parse the datetime string. Adjust format if necessary.
                # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                order['order_date'] = datetime.strptime(order['order_date'], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                app.logger.warning(f"Could not parse order_date string: {order['order_date']} for order ID: {order['id']}")
                # Optionally, set to None so the template logic `if order.order_date` still works
                # order['order_date'] = None # Or keep as string
        # ---- END MODIFICATION ----

        order_items = conn.execute('''
            SELECT oi.quantity, oi.price, p.name, p.image, p.id as product_id
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (order_id,)).fetchall()

    return render_template('admin/order_details.html', order=order, order_items=order_items, title=f'Order #{order_id}')


@app.route('/admin/orders/update_status/<int:order_id>', methods=['POST'])
@login_required
@admin_required
def admin_update_order_status(order_id):
    # CSRF token validation is implicitly handled by Flask-WTF if this was a WTForm submission.
    # Since it's a simple form in the template, ensure your template includes the CSRF token
    # and your main app setup has CSRFProtect(app).
    new_status = request.form.get('status')
    # Consider defining valid_statuses more globally or in app.config
    valid_statuses = ['Pending', 'Pending Payment', 'Processing', 'Paid', 'Shipped', 'Delivered', 'Cancelled', 'Refunded', 'Payment Failed'] 
    
    if new_status not in valid_statuses:
        flash('Invalid order status selected.', 'danger')
        return redirect(url_for('admin_order_details', order_id=order_id))
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
            conn.commit()
        flash(f'Order #{order_id} status updated to {new_status}.', 'success')
        log_activity("Admin updated order status", user_id=current_user.id, details=f"Order ID: {order_id}, New Status: {new_status}")
    except Exception as e:
        app.logger.error(f"Error updating order status for {order_id}: {e}")
        flash(f'Error updating order status: {str(e)}', 'danger')
        log_activity("Admin update order status failed", user_id=current_user.id, details=f"Order ID: {order_id}, Error: {str(e)}")
    return redirect(url_for('admin_order_details', order_id=order_id))


# Make sure datetime is imported: from datetime import datetime

@app.route('/admin/activity_logs')
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
            log_dict = dict(row) # Convert sqlite3.Row to a mutable dictionary
            if log_dict.get('timestamp') and isinstance(log_dict['timestamp'], str):
                try:
                    # Attempt to parse the datetime string. Adjust format if necessary.
                    # Common SQLite DATETIME format is '%Y-%m-%d %H:%M:%S'
                    log_dict['timestamp'] = datetime.strptime(log_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    app.logger.warning(f"Could not parse timestamp string: {log_dict['timestamp']} for log ID: {log_dict['id']}")
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
        endpoint='admin_activity_logs',
        query_params=pagination_query_params
    )



# app.py - Admin Routes section
# Ensure you have these imports if not already at the top of app.py for admin routes:
# from werkzeug.utils import secure_filename
# import os (already there)

# --- Admin Promotion Management ---
@app.route('/admin/promotions')
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
                app.logger.warning(f"Could not parse created_at for promotion ID: {promo_dict['id']}")
        promotions_list.append(promo_dict)

    return render_template('admin/promotions_list.html', promotions=promotions_list, title="Manage Promotions")

# app.py (ensure all necessary imports are at the top: os, secure_filename, current_app, etc.)

# ... (after PromotionForm definition and other routes) ...

class PromotionForm(FlaskForm):
    print("--- Defining PromotionForm class ---") # DEBUG: To see if this part of code is reached on startup
    title = StringField('Promotion Title', validators=[DataRequired(), Length(max=100)])
    image = FileField('Promotion Image', 
                      validators=[Optional(), 
                                  FileAllowed(['jpg', 'png', 'jpeg', 'gif'], 'Images only!')])
    target_url = StringField('Target URL (e.g., /category/electronics)', 
                             validators=[Optional(), Length(max=255)])
    alt_text = StringField('Image Alt Text (for accessibility)', 
                           validators=[Optional(), Length(max=150)])
    
    original_price = FloatField('Featured Original Price (Optional, for display on banner)', 
                                validators=[Optional(), NumberRange(min=0)])
    promo_price = FloatField('Featured Promotional Price (Optional, for display on banner)', 
                             validators=[Optional(), NumberRange(min=0)])
    discount_text = StringField('Discount Text (e.g., "50% OFF", "Summer Sale")', 
                                validators=[Optional(), Length(max=50)])
    
    is_active = BooleanField('Active (Show on Homepage)', default=True)
    display_order = IntegerField('Display Order (0 is first)', default=0, 
                                 validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField('Save Promotion')
    print("--- PromotionForm class defined ---") # DEBUG

# ... (ProductForm and other forms) ...


# --- ADMIN PROMOTION ROUTES ---
@app.route('/admin/promotions/add', methods=['GET', 'POST'])
@login_required
@admin_required # Assuming you have this decorator
def admin_add_promotion():
    print("\n--- admin_add_promotion route called ---") # DEBUG
    form = PromotionForm()
    print("--- PromotionForm instance created. Fields are: ---") # DEBUG
    for field in form: # DEBUG
        print(f"Field Name: {field.name}, Label: {field.label.text}, Type: {type(field)}") # DEBUG
    print("--- End of form fields list ---") # DEBUG

    if form.validate_on_submit():
        print("Form validated successfully on POST.") # DEBUG
        image_filename_for_db = None
        if form.image.data and form.image.data.filename:
            image_file = form.image.data
            if allowed_file(image_file.filename): # Make sure allowed_file is defined
                filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                # Ensure PROMOTIONS_UPLOAD_FOLDER is defined in app.config
                promotions_folder = current_app.config.get('PROMOTIONS_UPLOAD_FOLDER', os.path.join(current_app.config.get('BASE_DIR', '.'), 'static', 'uploads', 'promotions'))
                os.makedirs(promotions_folder, exist_ok=True) # Ensure folder exists
                save_path = os.path.join(promotions_folder, filename)
                try:
                    image_file.save(save_path)
                    image_filename_for_db = f"uploads/promotions/{filename}" # Path relative to static/
                    print(f"Image saved: {image_filename_for_db}") # DEBUG
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
        print(f"Data to insert into DB: {promo_data_to_save}") # DEBUG

        try:
            with get_db_connection() as conn:
                conn.execute('''
                    INSERT INTO promotions (title, image_path, target_url, alt_text, 
                                            original_price, promo_price, discount_text, 
                                            is_active, display_order, created_at)
                    VALUES (:title, :image_path, :target_url, :alt_text, 
                            :original_price, :promo_price, :discount_text, 
                            :is_active, :display_order, CURRENT_TIMESTAMP)
                ''', promo_data_to_save)
                conn.commit()
            flash('Promotion added successfully!', 'success')
            # log_activity("Admin added promotion", user_id=current_user.id, details=f"Title: {form.title.data}")
            print("Promotion added to DB successfully.") # DEBUG
            return redirect(url_for('admin_promotions'))
        except sqlite3.Error as e_sql:
            current_app.logger.error(f"Database error adding promotion: {e_sql}")
            flash(f'Database error adding promotion: {str(e_sql)}', 'danger')
            print(f"DB Error on INSERT: {e_sql}") # DEBUG
        except Exception as e_generic:
            current_app.logger.error(f"Unexpected error adding promotion: {e_generic}", exc_info=True)
            flash(f'An unexpected error occurred: {str(e_generic)}', 'danger')
            print(f"Generic Error on INSERT: {e_generic}") # DEBUG
    else:
        if request.method == 'POST':
            print(f"Form validation failed. Errors: {form.errors}") # DEBUG

    print("Rendering add_edit_promotion.html for 'Add' action.") # DEBUG
    return render_template('admin/add_edit_promotion.html', form=form, title='Add Promotion', action='Add', promotion=None)


@app.route('/admin/promotions/edit/<int:promo_id>', methods=['GET', 'POST'])
@login_required
@admin_required # Assuming you have this decorator
def admin_edit_promotion(promo_id):
    print(f"\n--- admin_edit_promotion route called for promo_id: {promo_id} ---") # DEBUG
    promotion_data_dict = None
    with get_db_connection() as conn:
        promotion_data_raw = conn.execute('SELECT * FROM promotions WHERE id = ?', (promo_id,)).fetchone()
        if promotion_data_raw:
            promotion_data_dict = dict(promotion_data_raw)
            print(f"Fetched promotion data from DB: {promotion_data_dict}") # DEBUG

    if not promotion_data_dict:
        flash('Promotion not found.', 'danger')
        return redirect(url_for('admin_promotions'))

    form = PromotionForm() # Create empty form first
    
    print("--- PromotionForm instance created for edit. Fields are: ---") # DEBUG
    for field in form: # DEBUG
        print(f"Field Name: {field.name}, Label: {field.label.text}, Type: {type(field)}") # DEBUG
    print("--- End of form fields list for edit ---") # DEBUG

    if form.validate_on_submit(): # POST request
        print("Edit form validated successfully on POST.") # DEBUG
        new_image_filename_for_db = promotion_data_dict['image_path'] # Keep old image by default

        if form.image.data and form.image.data.filename: 
            image_file = form.image.data
            if allowed_file(image_file.filename):
                if promotion_data_dict['image_path']: # If there was an old image
                    # Define BASE_DIR or get from app.config
                    base_dir = current_app.config.get('BASE_DIR', os.path.dirname(os.path.abspath(__file__))) 
                    old_image_full_path = os.path.join(base_dir, 'static', promotion_data_dict['image_path'])
                    if os.path.exists(old_image_full_path) and "default" not in promotion_data_dict['image_path'].lower() : # Don't delete default
                        try:
                            os.remove(old_image_full_path)
                            print(f"Old image deleted: {old_image_full_path}") # DEBUG
                        except OSError as e_remove:
                            current_app.logger.error(f"Error deleting old promotion image {old_image_full_path}: {e_remove}")
                
                filename = f"{uuid.uuid4().hex[:8]}_{secure_filename(image_file.filename)}"
                promotions_folder = current_app.config.get('PROMOTIONS_UPLOAD_FOLDER', os.path.join(current_app.config.get('BASE_DIR', '.'), 'static', 'uploads', 'promotions'))
                os.makedirs(promotions_folder, exist_ok=True)
                save_path = os.path.join(promotions_folder, filename)
                try:
                    image_file.save(save_path)
                    new_image_filename_for_db = f"uploads/promotions/{filename}"
                    print(f"New image saved: {new_image_filename_for_db}") # DEBUG
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
        print(f"Data to update in DB: {promo_data_to_update}") # DEBUG

        try:
            with get_db_connection() as conn:
                conn.execute('''
                    UPDATE promotions SET title=:title, image_path=:image_path, target_url=:target_url, 
                                        alt_text=:alt_text, original_price=:original_price, 
                                        promo_price=:promo_price, discount_text=:discount_text,
                                        is_active=:is_active, display_order=:display_order
                    WHERE id=:promo_id
                ''', promo_data_to_update)
                conn.commit()
            flash('Promotion updated successfully!', 'success')
            # log_activity("Admin edited promotion", user_id=current_user.id, details=f"Promotion ID: {promo_id}")
            print("Promotion updated in DB successfully.") # DEBUG
            return redirect(url_for('admin_promotions'))
        except sqlite3.Error as e_sql:
            current_app.logger.error(f"Database error updating promotion {promo_id}: {e_sql}")
            flash(f'Database error updating promotion: {str(e_sql)}', 'danger')
            print(f"DB Error on UPDATE: {e_sql}") # DEBUG
        except Exception as e_generic:
            current_app.logger.error(f"Unexpected error updating promotion {promo_id}: {e_generic}", exc_info=True)
            flash(f'An unexpected error occurred: {str(e_generic)}', 'danger')
            print(f"Generic Error on UPDATE: {e_generic}") # DEBUG
            
    elif request.method == 'GET': # Populate form for GET request
        print("Populating form for GET request (edit)") # DEBUG
        form.title.data = promotion_data_dict['title']
        form.target_url.data = promotion_data_dict['target_url']
        form.alt_text.data = promotion_data_dict['alt_text']
        form.original_price.data = promotion_data_dict.get('original_price') # Use .get() for safety
        form.promo_price.data = promotion_data_dict.get('promo_price')
        form.discount_text.data = promotion_data_dict.get('discount_text')
        form.is_active.data = bool(promotion_data_dict['is_active'])
        form.display_order.data = promotion_data_dict['display_order']
        print(f"Form populated with: original_price={form.original_price.data}, promo_price={form.promo_price.data}, discount_text='{form.discount_text.data}'") # DEBUG
        print(f"Rendering add_edit_promotion.html for 'Edit' action. Promotion data: {promotion_data_dict}") # DEBUG
        return render_template(
            'admin/add_edit_promotion.html',
            form=form,
            title='Edit Promotion',
            action='Edit',
            promotion=promotion_data_dict  # Pass promotion_data_dict for displaying current image
        )

    # POST that failed validation (or failed to save): show the form again with its errors
    return render_template(
        'admin/add_edit_promotion.html',
        form=form,
        title='Edit Promotion',
        action='Edit',
        promotion=promotion_data_dict
    )

@app.route('/admin/promotions/delete/<int:promo_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_promotion(promo_id):
    try:
        with get_db_connection() as conn:
            promotion_data = conn.execute("SELECT image_path FROM promotions WHERE id = ?", (promo_id,)).fetchone()
            if promotion_data and promotion_data['image_path']:
                image_file_path = os.path.join(BASE_DIR, 'static', promotion_data['image_path'])
                if os.path.exists(image_file_path):
                    try:
                        os.remove(image_file_path)
                    except OSError as e:
                        app.logger.error(f"Error deleting promotion image file {image_file_path}: {e}")
                        flash(f"Could not delete image file: {e}", "warning")
            
            conn.execute("DELETE FROM promotions WHERE id = ?", (promo_id,))
            conn.commit()
        flash('Promotion deleted successfully.', 'success')
        log_activity("Admin deleted promotion", user_id=current_user.id, details=f"Promotion ID: {promo_id}")
    except Exception as e:
        app.logger.error(f"Error deleting promotion {promo_id}: {e}")
        flash(f'Error deleting promotion: {str(e)}', 'danger')
    return redirect(url_for('admin_promotions'))


# --- Checkout and Payment Routes ---
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
        auth_response = requests.get(auth_url, auth=(app.config['MPESA_CONSUMER_KEY'], app.config['MPESA_CONSUMER_SECRET']))
        auth_response.raise_for_status() # Raise HTTPError for bad responses (4XX or 5XX)
        access_token = auth_response.json()['access_token']

        # 2. Prepare STK Push Request
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_string = app.config['MPESA_BUSINESS_SHORT_CODE'] + app.config['MPESA_PASSKEY'] + timestamp
        password = base64.b64encode(password_string.encode()).decode()

        stk_push_url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        headers = {'Authorization': f'Bearer {access_token}'}
        payload = {
            "BusinessShortCode": app.config['MPESA_BUSINESS_SHORT_CODE'],
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline", # or "CustomerBuyGoodsOnline" depending on your shortcode type
            "Amount": amount,
            "PartyA": phone_number, # Customer's phone number
            "PartyB": app.config['MPESA_BUSINESS_SHORT_CODE'], # Your organization's shortcode
            "PhoneNumber": phone_number, # Customer's phone number again
            "CallBackURL": app.config['MPESA_CALLBACK_URL'] + f"?order_id={order_id_ref}", # Include order_id for tracking
            "AccountReference": f"ORDER-{order_id_ref}", # Custom reference for the transaction
            "TransactionDesc": f"Payment for Order #{order_id_ref}"
        }

        response = requests.post(stk_push_url, json=payload, headers=headers)
        response.raise_for_status()
        log_activity("M-Pesa STK Push initiated", details=f"Order ID: {order_id_ref}, Response: {response.json()}")
        return response.json() # Contains CheckoutRequestID, ResponseCode, etc.

    except requests.exceptions.RequestException as e:
        app.logger.error(f"M-Pesa RequestException: {e} - Response: {e.response.text if e.response else 'No response'}")
        log_activity("M-Pesa STK Push failed", details=f"Order ID: {order_id_ref}, Error: {str(e)}")
        return {"ResponseCode": "1", "ResponseDescription": f"Network or API Error: {str(e)}"}
    except Exception as e:
        app.logger.error(f"M-Pesa general error: {e}")
        log_activity("M-Pesa STK Push failed", details=f"Order ID: {order_id_ref}, Error: {str(e)}")
        return {"ResponseCode": "1", "ResponseDescription": f"An unexpected error occurred: {str(e)}"}


@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    form = CheckoutForm()
    with get_db_connection() as conn:
        cart_items_raw = conn.execute('''
            SELECT p.id as product_id, p.name, p.price, p.stock, c.quantity
            FROM carts c JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        ''', (current_user.id,)).fetchall()

    if not cart_items_raw:
        flash('Your cart is empty. Add some products before checking out!', 'warning')
        return redirect(url_for('index'))

    # Validate stock for all cart items before proceeding
    for item in cart_items_raw:
        if item['quantity'] > item['stock']:
            flash(f"Not enough stock for {item['name']}. Only {item['stock']} available. Please update your cart.", 'danger')
            return redirect(url_for('view_cart'))

    total_amount = sum(item['price'] * item['quantity'] for item in cart_items_raw)

    if form.validate_on_submit():
        payment_method = form.payment_method.data
        shipping_address = form.shipping_address.data
        # Begin transaction
        conn = get_db_connection()
        try:
            with conn: # Context manager handles commit or rollback
                # 1. Create Order
                order_cursor = conn.execute('''
                    INSERT INTO orders (user_id, total_amount, payment_method, shipping_address, status)
                    VALUES (?, ?, ?, ?, 'Pending')
                ''', (current_user.id, total_amount, payment_method, shipping_address))
                order_id = order_cursor.lastrowid

                # 2. Create Order Items and Update Product Stock
                for item in cart_items_raw:
                    conn.execute('''
                        INSERT INTO order_items (order_id, product_id, quantity, price)
                        VALUES (?, ?, ?, ?)
                    ''', (order_id, item['product_id'], item['quantity'], item['price']))
                    # Decrease stock
                    new_stock = item['stock'] - item['quantity']
                    conn.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, item['product_id']))

                # 3. Handle Payment (Example for M-Pesa)
                transaction_id = None
                if payment_method == 'Lipa na M-Pesa':
                    mpesa_phone = form.phone_number.data
                    if not mpesa_phone:
                        flash('M-Pesa phone number is required for this payment method.', 'danger')
                        raise ValueError("M-Pesa phone number missing") # Will trigger rollback

                    mpesa_response = lipa_na_mpesa_stk_push(total_amount, mpesa_phone, order_id)
                    if mpesa_response.get('ResponseCode') == '0': # STK Push initiated successfully
                        transaction_id = mpesa_response.get('CheckoutRequestID')
                        conn.execute("UPDATE orders SET transaction_id = ?, status = 'Pending Payment' WHERE id = ?", (transaction_id, order_id))
                        flash('M-Pesa STK Push initiated. Please complete the payment on your phone.', 'info')
                        # Order remains 'Pending Payment' until callback confirms
                    else:
                        error_desc = mpesa_response.get('ResponseDescription', 'M-Pesa payment initiation failed.')
                        flash(f'M-Pesa Error: {error_desc}', 'danger')
                        raise Exception(f"M-Pesa STK Push failed: {error_desc}") # Trigger rollback

                elif payment_method == 'Cash on Delivery':
                     conn.execute("UPDATE orders SET status = 'Processing' WHERE id = ?", (order_id,)) # Or 'Pending' then admin confirms
                     flash('Order placed successfully with Cash on Delivery! We will contact you shortly.', 'success')

                else: # Other payment methods like 'Credit Card', 'PayPal' would need their own integration
                     conn.execute("UPDATE orders SET status = 'Processing' WHERE id = ?", (order_id,)) # Placeholder
                     flash('Order placed successfully! (Payment method needs integration)', 'success')

                # 4. Clear Cart (only if everything up to payment initiation was successful)
                conn.execute('DELETE FROM carts WHERE user_id = ?', (current_user.id,))
                # Commit is handled by 'with conn:' if no exceptions

            log_activity("Order placed", user_id=current_user.id, details=f"Order ID: {order_id}, Total: {total_amount}, Payment: {payment_method}")
            if payment_method == 'Lipa na M-Pesa' and transaction_id:
                 return redirect(url_for('order_details', order_id=order_id)) # Show pending order details
            return redirect(url_for('order_history')) # Or a specific thank you page

        except ValueError as ve: # Specific error like missing phone number
            # flash already handled
            return render_template('checkout.html', form=form, cart_items=cart_items_raw, total_amount=total_amount, title='Checkout')
        except Exception as e:
            # conn.rollback() is handled by 'with conn:'
            flash(f'Error placing order: {str(e)}', 'danger')
            log_activity("Order placement failed", user_id=current_user.id, details=str(e))
            return render_template('checkout.html', form=form, cart_items=cart_items_raw, total_amount=total_amount, title='Checkout')
        finally:
            conn.close() # Close connection explicitly if not using global app context connection management

    # GET request or form validation failed
    return render_template('checkout.html', form=form, cart_items=cart_items_raw, total_amount=total_amount, title='Checkout')

@app.route('/mpesa_callback', methods=['POST'])
@csrf.exempt # M-Pesa callbacks won't have CSRF token
def mpesa_callback():
    # This is where Safaricom will send the STK Push result
    callback_data = request.get_json()
    app.logger.info(f"M-Pesa Callback Received: {callback_data}")
    log_activity("M-Pesa callback received", details=str(callback_data))

    order_id_ref = request.args.get('order_id') # Get order_id from query param we set in CallBackURL

    try:
        result_code = callback_data['Body']['stkCallback']['ResultCode']
        checkout_request_id = callback_data['Body']['stkCallback']['CheckoutRequestID']

        with get_db_connection() as conn:
            order = conn.execute("SELECT * FROM orders WHERE transaction_id = ? AND id = ?",
                                 (checkout_request_id, order_id_ref)).fetchone()
            if not order:
                app.logger.error(f"M-Pesa Callback: Order not found for CheckoutRequestID {checkout_request_id} and Order Ref {order_id_ref}")
                return jsonify({"ResultCode": 1, "ResultDesc": "Order not found"}), 404 # Or 200 for M-Pesa

            if result_code == 0:
                # Payment successful
                # StkCallback has Item array with MpesaReceiptNumber, Amount, etc.
                # callback_metadata = callback_data['Body']['stkCallback']['CallbackMetadata']['Item']
                # mpesa_receipt = next((item['Value'] for item in callback_metadata if item['Name'] == 'MpesaReceiptNumber'), None)

                conn.execute("UPDATE orders SET status = 'Paid' WHERE id = ?", (order['id'],))
                # Optionally, store MpesaReceiptNumber in orders table if you add a column for it
                conn.commit()
                log_activity("M-Pesa payment successful", user_id=order['user_id'], details=f"Order ID: {order['id']}, M-Pesa Data: {callback_data}")
                # TODO: Send order confirmation email to user
            else:
                # Payment failed or cancelled
                error_desc = callback_data['Body']['stkCallback'].get('ResultDesc', 'Payment failed.')
                conn.execute("UPDATE orders SET status = 'Payment Failed' WHERE id = ?", (order['id'],))
                conn.commit()
                log_activity("M-Pesa payment failed/cancelled", user_id=order['user_id'], details=f"Order ID: {order['id']}, M-Pesa Data: {callback_data}")
                # TODO: Notify user of payment failure

        # Acknowledge receipt to M-Pesa
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200

    except Exception as e:
        app.logger.error(f"M-Pesa Callback Error: {str(e)} - Data: {callback_data}")
        log_activity("M-Pesa callback processing error", details=str(e))
        # Acknowledge receipt even on error to prevent M-Pesa retries if it's a processing fault on our side
        return jsonify({"ResultCode": 1, "ResultDesc": "Internal Server Error"}), 200 # Or 500 but M-Pesa might prefer 200

@app.route('/orders')
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
        order_date = order.get('order_date')
        if order_date and isinstance(order_date, str):
            try:
                dt = datetime.strptime(order_date, '%Y-%m-%d %H:%M:%S')
                order['order_date_formatted'] = dt.strftime('%B %d, %Y, %I:%M %p')
            except Exception:
                order['order_date_formatted'] = order_date
        else:
            order['order_date_formatted'] = 'N/A'
        orders.append(order)

    return render_template('order_history.html', orders=orders, title='Your Orders') 


@app.route('/orders/<int:order_id>') # This is order_details for the user
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
            return redirect(url_for('order_history'))

        order_items = conn.execute('''
            SELECT oi.quantity, oi.price, p.name, p.image, p.id as product_id
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (order_id,)).fetchall()

    # Build a dict so we can add a human-friendly date for the template
    order_dict = dict(order)
    raw_date = order_dict.get('order_date')
    try:
        if isinstance(raw_date, str):
            raw_date = datetime.strptime(raw_date.split('.')[0], '%Y-%m-%d %H:%M:%S')
        order_dict['order_date_formatted'] = raw_date.strftime('%b %d, %Y %H:%M')
    except Exception:
        order_dict['order_date_formatted'] = order_dict.get('order_date') or 'N/A'

    return render_template('order_details_user.html', order=order_dict,
                           order_items=order_items, title=f"Order #{order_id}")


# app.py
# Ensure these imports are at the top of your file:
from flask import render_template, request, jsonify, current_app # Added jsonify
from flask_login import login_required
from datetime import datetime, timedelta # Added timedelta
import json # For safely embedding data into JavaScript

# ... (your existing imports, app setup, get_db_connection, admin_required decorator) ...

@app.route('/admin/reports/sales-over-time', methods=['GET'])
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
            sales_by_day = {row['sale_day']: {'total': row['daily_total'], 'orders': row['daily_orders']} for row in raw_sales_data}

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
                
    except sqlite3.Error as e:
        current_app.logger.error(f"Database error fetching sales report: {e}")
        flash("Error fetching sales report data.", "danger")
    except Exception as e_gen:
        current_app.logger.error(f"Unexpected error fetching sales report: {e_gen}", exc_info=True)
        flash("An unexpected error occurred.", "danger")

    # Debug prints (optional, can be removed in production)
    print(f"--- DEBUG: report_sales_over_time ---")
    print(f"Python list 'labels': {labels}")
    print(f"Python list 'data_points': {data_points}")
    print(f"------------------------------------")

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
# app.py
# Ensure these imports are at the top:
# from flask import render_template, request, current_app, flash
# from flask_login import login_required
# from datetime import datetime, timedelta
# import sqlite3
# from .utils import get_db_connection, admin_required # Assuming these

@app.route('/admin/reports/sales-by-product', methods=['GET'])
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

    except sqlite3.Error as e:
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
# app.py
@app.route('/admin/reports/low-stock', methods=['GET'])
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
    except sqlite3.Error as e:
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

if __name__ == '__main__':
    # CLI command to initialize the DB
    @app.cli.command('init-db')
    def init_db_command():
        """Initializes the database by creating tables."""
        init_db() # Defined in Part 1
        print('Initialized the database.')

    # ... (all your routes and other app logic) ...

# --- Application Runner ---

# CLI command to initialize the DB
@app.cli.command('init-db')
def init_db_command():
    """Initializes the database by creating tables."""
    print("Attempting to initialize database...") # Add print statement
    try:
        init_db() # Your function from Part 1
        print("Database initialization process completed.")
    except Exception as e:
        print(f"An error occurred during init_db: {e}")
        # Optionally re-raise e if you want flask CLI to show it as an error
# CLI command to create a default admin user (optional, but good to have)
# In app.py (usually near the end, with other CLI commands)
# Make sure generate_password_hash, get_db_connection, log_activity, sqlite3 are imported/available

@app.cli.command('create-admin')
def create_admin_command():
    """Creates a default admin user."""
    username_input = input("Enter admin username: ").strip()
    email_input = input("Enter admin email: ").strip()
    password_input = input("Enter admin password: ") # Don't strip password, spaces might be intentional

    if not all([username_input, email_input, password_input]):
        print("Username, email, and password are required.")
        return

    hashed_password = generate_password_hash(password_input)
    try:
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO users (username, email, password_hash, is_admin, confirmed, created_at) VALUES (?, ?, ?, 1, 1, CURRENT_TIMESTAMP)',
                (username_input, email_input, hashed_password)
            )
            conn.commit()
        # Only print and log if the above was successful
        print(f"Admin user '{username_input}' created successfully.")
        log_activity("Admin user created via CLI", details=f"Username: {username_input}")
    except sqlite3.IntegrityError:
        # username_input and email_input are guaranteed to be defined here
        print(f"Admin user with username '{username_input}' or email '{email_input}' already exists.")
    except Exception as e:
        print(f"Error creating admin user: {str(e)}")
        # Log the exception for more details if needed
        # app.logger.error(f"CLI create-admin failed: {e}", exc_info=True)
if __name__ == '__main__':
    # Debug mode exposes an interactive console - only enable it locally: FLASK_DEBUG=1
    app.run(debug=os.environ.get('FLASK_DEBUG', '0') == '1',
            host=os.environ.get('FLASK_HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', 5000)))