"""Accounts: register, verify email, login/logout, password reset."""
from shop.views.account import start_two_factor_login
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import generate_password_hash
from shop.db import IntegrityError, get_db_connection
from shop.extensions import token_serializer
from shop.forms import LoginForm, RegistrationForm, RequestResetForm, ResetPasswordForm, VerifyEmailForm
from shop.models import User, end_other_sessions
from shop.notifications import _password_fingerprint, send_password_reset_email, send_verification_email
from shop.security import LOGIN_LOCK_MINUTES, LOGIN_MAX_FAILS_PER_ACCOUNT, LOGIN_MAX_FAILS_PER_IP, clear_rate_events, client_ip, is_safe_next_url, log_activity, rate_limited, record_rate_event


bp = Blueprint('auth', __name__)


MAX_VERIFICATION_ATTEMPTS = 5


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def is_allowed_registration_email(email):
    allowed = current_app.config.get('ALLOWED_REGISTER_EMAILS', set())
    normalized = (email or '').strip().lower()
    if not normalized:
        return False
    if not allowed:
        return True
    return normalized in allowed


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('store.index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        email_value = form.email.data.strip().lower()
        if rate_limited('register_ip', client_ip(), 10, 60):
            flash('Too many sign-ups from your network. Please try again later.', 'warning')
            return render_template('register.html', title='Register', form=form)
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
            record_rate_event('register_ip', client_ip())
            sent = send_verification_email(email_value, code)
            if sent:
                flash('Registration successful! Check your email for the 6-digit verification code.', 'success')
            else:
                flash('Registration successful, but the verification email could not be delivered. Please contact support.', 'warning')
            log_activity("User registration initiated", details=f"Username: {form.username.data}")
            return redirect(url_for('auth.verify_email', email=email_value))
        except IntegrityError:
            with get_db_connection() as conn:
                existing_username = conn.execute('SELECT id FROM users WHERE username = ?', (form.username.data.strip(),)).fetchone()
                existing_email = conn.execute('SELECT id FROM users WHERE lower(email) = lower(?)', (email_value,)).fetchone()
            if existing_username and existing_email:
                flash('This username and email are already registered.', 'danger')
            elif existing_username:
                flash('This username is already taken.', 'danger')
            else:
                flash('This email is already registered.', 'danger')
        except Exception as e:
            flash('Sorry, something went wrong. Please try again.', 'danger')
            log_activity("User registration failed", details=f"Username: {form.username.data}, Error: {str(e)}")
    return render_template('register.html', title='Register', form=form)


@bp.route('/verify_email', methods=['GET', 'POST'])
def verify_email():
    default_email = request.args.get('email', '')
    form = VerifyEmailForm(email=default_email)
    if form.validate_on_submit():
        email = form.email.data.strip()
        submitted_code = form.verification_code.data.strip()

        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()
            if not user:
                flash('No account found for that email.', 'danger')
                return redirect(url_for('auth.register'))

            if user['confirmed']:
                flash('Account already confirmed. Please login.', 'info')
                return redirect(url_for('auth.login'))

            expires_at = user['verification_code_expires']
            if not expires_at or datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S') < datetime.utcnow():
                new_code = generate_verification_code()
                new_expires_at = (datetime.utcnow() + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute(
                    'UPDATE users SET verification_code = ?, verification_code_expires = ?, verification_attempts = 0 WHERE id = ?',
                    (new_code, new_expires_at, user['id'])
                )
                conn.commit()
                send_verification_email(email, new_code)
                flash('Your verification code expired. A new one has been sent to your email.', 'warning')
                return redirect(url_for('auth.verify_email', email=email))

            if not secrets.compare_digest(submitted_code, user['verification_code'] or ''):
                attempts = (user['verification_attempts'] or 0) + 1
                if attempts >= MAX_VERIFICATION_ATTEMPTS:
                    # Too many wrong guesses: throw this code away and send a fresh one
                    new_code = generate_verification_code()
                    new_expires_at = (datetime.utcnow() + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
                    conn.execute(
                        'UPDATE users SET verification_code = ?, verification_code_expires = ?, verification_attempts = 0 WHERE id = ?',
                        (new_code, new_expires_at, user['id'])
                    )
                    conn.commit()
                    send_verification_email(user['email'], new_code)
                    log_activity("Verification code reset after failed attempts", details=f"Email: {user['email']}")
                    flash('Too many incorrect codes. We sent a new code to your email.', 'warning')
                    return redirect(url_for('auth.verify_email', email=user['email']))
                conn.execute('UPDATE users SET verification_attempts = ? WHERE id = ?', (attempts, user['id']))
                conn.commit()
                flash(f'Invalid verification code. {MAX_VERIFICATION_ATTEMPTS - attempts} attempt(s) left.', 'danger')
                return render_template('verify_email.html', title='Verify Email', form=form)

            conn.execute(
                'UPDATE users SET confirmed = 1, verification_code = NULL, verification_code_expires = NULL, verification_attempts = 0 WHERE id = ?',
                (user['id'],)
            )
            conn.commit()

        flash('Your account has been verified! You can now log in.', 'success')
        log_activity("Email verified", details=f"Email: {email}")
        return redirect(url_for('auth.login'))

    return render_template('verify_email.html', title='Verify Email', form=form)


@bp.route('/verify_email/resend', methods=['POST'])
def resend_verification_code():
    """Send a fresh verification code (at most one per minute per account)."""
    email = (request.form.get('email') or '').strip().lower()
    if email:
        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()
            if user and not user['confirmed']:
                expires = user['verification_code_expires']
                issued_at = (datetime.strptime(expires, '%Y-%m-%d %H:%M:%S') - timedelta(minutes=15)) if expires else None
                if issued_at and datetime.utcnow() - issued_at < timedelta(seconds=60):
                    flash('Please wait a minute before asking for another code.', 'warning')
                    return redirect(url_for('auth.verify_email', email=email))
                code = generate_verification_code()
                new_expires = (datetime.utcnow() + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute('UPDATE users SET verification_code = ?, verification_code_expires = ?, verification_attempts = 0 WHERE id = ?',
                             (code, new_expires, user['id']))
                conn.commit()
                send_verification_email(user['email'], code)
    # Same message whether or not the account exists
    flash('If that account is waiting for verification, a new code has been sent.', 'info')
    return redirect(url_for('auth.verify_email', email=email))


@bp.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = token_serializer().loads(token, salt='email-confirm', max_age=3600) # 1 hour expiry
    except Exception: # Catches SignatureExpired, BadTimeSignature etc.
        flash('The confirmation link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.register'))

    with get_db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('auth.register'))
        if user['confirmed']:
            flash('Account already confirmed. Please login.', 'info')
            return redirect(url_for('auth.login'))
        conn.execute('UPDATE users SET confirmed = 1 WHERE lower(email) = lower(?)', (email,))
        conn.commit()
    flash('Your account has been confirmed! You can now log in.', 'success')
    log_activity("Email confirmed", details=f"Email: {email}")
    return redirect(url_for('auth.login'))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('store.index'))
    form = LoginForm()
    if form.validate_on_submit():
        login_identifier = form.login_identifier.data
        password = form.password.data
        with get_db_connection() as conn:
            # Check if login_identifier is an email or username
            if '@' in login_identifier:
                user_data = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (login_identifier,)).fetchone()
            else:
                user_data = conn.execute("SELECT * FROM users WHERE username = ?", (login_identifier,)).fetchone()

            # Too many wrong passwords for this account or from this network? Make them wait.
            account_key = f"user:{user_data['id']}" if user_data else f"name:{login_identifier.strip().lower()}"
            ip = client_ip()
            if (rate_limited('login_fail_account', account_key, LOGIN_MAX_FAILS_PER_ACCOUNT, LOGIN_LOCK_MINUTES)
                    or rate_limited('login_fail_ip', ip, LOGIN_MAX_FAILS_PER_IP, LOGIN_LOCK_MINUTES)):
                log_activity('Login blocked (too many failed attempts)', user_id=user_data['id'] if user_data else None, details=f"IP: {ip}")
                flash(f'Too many failed login attempts. Please wait {LOGIN_LOCK_MINUTES} minutes, or reset your password.', 'danger')
                return render_template('login.html', title='Login', form=form)

            if user_data:
                user_obj = User(
                    id=user_data['id'], username=user_data['username'], email=user_data['email'],
                    password_hash=user_data['password_hash'], is_admin=user_data['is_admin'],
                    confirmed=user_data['confirmed']
                )
                if user_obj.verify_password(password):
                    clear_rate_events('login_fail_account', account_key)
                    if user_obj.confirmed and user_data['totp_enabled']:
                        # Password is right; now ask for the authenticator code before logging in
                        start_two_factor_login(user_obj.id, form.remember.data, request.args.get('next'))
                        return redirect(url_for('account.login_code'))
                    if user_obj.confirmed:
                        login_user(user_obj, remember=form.remember.data)
                        log_activity('Logged in', user_id=user_obj.id)
                        flash('Logged in successfully.', 'success')
                        next_page = request.args.get('next')
                        if not is_safe_next_url(next_page):
                            next_page = None
                        return redirect(next_page or url_for('store.index'))
                    else:
                        flash('Please verify your email with the 6-digit code before logging in.', 'warning')
                        return redirect(url_for('auth.verify_email', email=user_obj.email))
                else:
                    flash('Invalid username/email or password.', 'danger')
            else:
                flash('Invalid username/email or password.', 'danger')
            # Only reached on a failed login
            record_rate_event('login_fail_account', account_key)
            record_rate_event('login_fail_ip', ip)
            if user_data:
                log_activity('Failed login', user_id=user_data['id'], details=f"IP: {ip}")
    return render_template('login.html', title='Login', form=form)


@bp.route('/logout', methods=['POST']) # CORRECTED: Use POST for logout
@login_required
def logout():
    log_activity('Logged out', user_id=current_user.id)
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


@bp.route("/reset_password", methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('store.index'))
    form = RequestResetForm()
    if form.validate_on_submit():
        email_key = form.email.data.strip().lower()
        if rate_limited('reset_ip', client_ip(), 5, 60) or rate_limited('reset_email', email_key, 3, 60):
            flash('Too many reset requests. Please try again in an hour.', 'warning')
            return redirect(url_for('auth.login'))
        record_rate_event('reset_ip', client_ip())
        record_rate_event('reset_email', email_key)
        with get_db_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (form.email.data,)).fetchone()
        if user:
            send_password_reset_email(user['email'], user['password_hash'])
        # Same message either way so the form can't be used to check which emails have accounts
        flash('If an account with that email exists, a reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('reset_request.html', title='Reset Password', form=form)


@bp.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('store.index'))
    try:
        email, fingerprint = token_serializer().loads(token, salt='password-reset', max_age=3600) # 1 hour expiry
    except Exception:
        flash('That reset link is invalid or has expired.', 'warning')
        return redirect(url_for('auth.reset_request'))
    with get_db_connection() as conn:
        user = conn.execute("SELECT id, password_hash FROM users WHERE lower(email) = lower(?)", (email,)).fetchone()
    if not user or not secrets.compare_digest(fingerprint, _password_fingerprint(user['password_hash'])):
        flash('That reset link has already been used. Please request a new one.', 'warning')
        return redirect(url_for('auth.reset_request'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        hashed_password = generate_password_hash(form.password.data)
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed_password, user['id']))
            end_other_sessions(conn, user['id'])          # anyone logged in with the old password is logged out
            conn.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        log_activity("Password reset successfully", details=f"Email: {email}")
        return redirect(url_for('auth.login'))
    return render_template('reset_token.html', title='Reset Password', form=form)
