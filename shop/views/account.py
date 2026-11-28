"""Account security: two-step login with an authenticator app (TOTP) and recovery codes."""
import io
import json
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg
from flask import Blueprint, current_app, flash, redirect, render_template, session, url_for
from flask_login import current_user, login_required, login_user
from flask_wtf import FlaskForm
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length

from shop.db import get_db_connection
from shop.models import end_other_sessions, load_user, stay_logged_in
from shop.security import clear_rate_events, client_ip, is_safe_next_url, log_activity, rate_limited, record_rate_event

bp = Blueprint('account', __name__)

PENDING_KEY = '2fa_pending'          # session: password OK, waiting for the code
SETUP_KEY = '2fa_setup_secret'       # session: secret shown as a QR code, not confirmed yet
PENDING_SECONDS = 300                # the code must be entered within 5 minutes of the password
MAX_CODE_ATTEMPTS = 5                # per 15 minutes
RECOVERY_CODE_COUNT = 8


class CodeForm(FlaskForm):
    code = StringField('Code', validators=[DataRequired(), Length(min=6, max=20)])
    submit = SubmitField('Continue')


class DisableForm(FlaskForm):
    password = PasswordField('Your password', validators=[DataRequired()])
    code = StringField('Code from your app (or a recovery code)', validators=[DataRequired(), Length(min=6, max=20)])
    submit = SubmitField('Turn off two-step login')


def _clean(code):
    return (code or '').replace(' ', '').replace('-', '').strip().lower()


def _new_recovery_codes():
    """Returns (codes to show once, hashes to store)."""
    codes = [f'{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}' for _ in range(RECOVERY_CODE_COUNT)]
    return codes, json.dumps([generate_password_hash(_clean(c)) for c in codes])


def matching_step(secret, code):
    """The 30-second time step this authenticator code belongs to (now, or one step either side), or None."""
    totp, now = pyotp.TOTP(secret), time.time()
    for offset in (0, -1, 1):
        t = now + offset * totp.interval
        if secrets.compare_digest(totp.at(t), code):
            return int(t // totp.interval)
    return None


def check_second_factor(user_row, code):
    """True if `code` is the current authenticator code or an unused recovery code (which is then used up)."""
    code = _clean(code)
    if not user_row['totp_secret']:
        return False
    if code.isdigit() and len(code) == 6:
        step = matching_step(user_row['totp_secret'], code)
        if step is None:
            return False
        # Each code works only once: remember the newest time step used (in one step, so two
        # requests with the same code can't both pass)
        with get_db_connection() as conn:
            cur = conn.execute('UPDATE users SET totp_last_step = ? WHERE id = ? AND (totp_last_step IS NULL OR totp_last_step < ?)',
                               (step, user_row['id'], step))
        return cur.rowcount == 1
    hashes = json.loads(user_row['recovery_codes'] or '[]')
    for h in hashes:
        if check_password_hash(h, code):
            hashes.remove(h)
            with get_db_connection() as conn:
                conn.execute('UPDATE users SET recovery_codes = ? WHERE id = ?', (json.dumps(hashes), user_row['id']))
            log_activity('Recovery code used', user_id=user_row['id'], details=f'{len(hashes)} left')
            return True
    return False


def start_two_factor_login(user_id, remember, next_url):
    session[PENDING_KEY] = {'uid': user_id, 'remember': bool(remember),
                            'next': next_url if is_safe_next_url(next_url) else None, 'at': int(time.time())}


@bp.route('/login/code', methods=['GET', 'POST'])
def login_code():
    pending = session.get(PENDING_KEY)
    if not pending or time.time() - pending['at'] > PENDING_SECONDS:
        session.pop(PENDING_KEY, None)
        flash('Please log in again.', 'info')
        return redirect(url_for('auth.login'))
    form = CodeForm()
    if form.validate_on_submit():
        key = f"user:{pending['uid']}"
        if rate_limited('2fa_fail', key, MAX_CODE_ATTEMPTS, 15):
            session.pop(PENDING_KEY, None)
            flash('Too many wrong codes. Please wait 15 minutes and log in again.', 'danger')
            return redirect(url_for('auth.login'))
        with get_db_connection() as conn:
            row = conn.execute('SELECT * FROM users WHERE id = ?', (pending['uid'],)).fetchone()
        if row and check_second_factor(row, form.code.data):
            session.pop(PENDING_KEY, None)
            clear_rate_events('2fa_fail', key)
            login_user(load_user(row['id']), remember=pending['remember'])
            log_activity('Logged in (two-step)', user_id=row['id'], details=f'IP: {client_ip()}')
            flash('Logged in successfully.', 'success')
            return redirect(pending['next'] or url_for('store.index'))
        record_rate_event('2fa_fail', key)
        log_activity('Wrong two-step code', user_id=pending['uid'], details=f'IP: {client_ip()}')
        flash('That code is not right. Check your authenticator app and try again.', 'danger')
    return render_template('account/login_code.html', form=form, title='Enter your code')


@bp.route('/account/security')
@login_required
def security():
    with get_db_connection() as conn:
        row = conn.execute('SELECT totp_enabled, recovery_codes FROM users WHERE id = ?', (current_user.id,)).fetchone()
    codes_left = len(json.loads(row['recovery_codes'] or '[]')) if row['totp_enabled'] else 0
    return render_template('account/security.html', enabled=bool(row['totp_enabled']), codes_left=codes_left,
                           disable_form=DisableForm(), title='Account security')


@bp.route('/account/two-step/setup', methods=['GET', 'POST'])
@login_required
def setup():
    if current_user.totp_enabled:
        return redirect(url_for('account.security'))
    secret = session.get(SETUP_KEY) or pyotp.random_base32()
    session[SETUP_KEY] = secret
    form = CodeForm()
    if form.validate_on_submit():
        step = matching_step(secret, _clean(form.code.data))
        if step is not None:
            codes, hashes = _new_recovery_codes()
            with get_db_connection() as conn:
                conn.execute('UPDATE users SET totp_secret = ?, totp_enabled = 1, recovery_codes = ?, totp_last_step = ? WHERE id = ?',
                             (secret, hashes, step, current_user.id))
                end_other_sessions(conn, current_user.id)      # log out every other device
            stay_logged_in(current_user.id)
            session.pop(SETUP_KEY, None)
            log_activity('Two-step login turned on', user_id=current_user.id)
            return render_template('account/recovery_codes.html', codes=codes, title='Save your recovery codes')
        flash('That code is not right. Make sure the time on your phone is correct and try again.', 'danger')
    uri = pyotp.TOTP(secret).provisioning_uri(name=current_user.email, issuer_name=current_app.config['SITE_NAME'])
    buf = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2).save(buf)
    qr_svg = buf.getvalue().decode()
    qr_svg = qr_svg[qr_svg.index('<svg'):]                     # drop the XML header for inline use
    return render_template('account/setup.html', form=form, qr_svg=qr_svg,
                           secret=' '.join(secret[i:i + 4] for i in range(0, len(secret), 4)), title='Turn on two-step login')


@bp.route('/account/two-step/disable', methods=['POST'])
@login_required
def disable():
    form = DisableForm()
    with get_db_connection() as conn:
        row = conn.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()
    if form.validate_on_submit() and check_password_hash(row['password_hash'], form.password.data) \
            and not rate_limited('2fa_fail', f'user:{row["id"]}', MAX_CODE_ATTEMPTS, 15) \
            and check_second_factor(row, form.code.data):
        with get_db_connection() as conn:
            conn.execute('UPDATE users SET totp_secret = NULL, totp_enabled = 0, recovery_codes = NULL, totp_last_step = NULL WHERE id = ?',
                         (row['id'],))
            end_other_sessions(conn, row['id'])
        stay_logged_in(row['id'])
        log_activity('Two-step login turned off', user_id=row['id'])
        flash('Two-step login is now off.', 'warning')
    else:
        record_rate_event('2fa_fail', f'user:{row["id"]}')
        flash('Password or code is not right - two-step login is still on.', 'danger')
    return redirect(url_for('account.security'))


@bp.route('/account/two-step/new-codes', methods=['POST'])
@login_required
def new_recovery_codes():
    if not current_user.totp_enabled:
        return redirect(url_for('account.security'))
    codes, hashes = _new_recovery_codes()
    with get_db_connection() as conn:
        conn.execute('UPDATE users SET recovery_codes = ? WHERE id = ?', (hashes, current_user.id))
    log_activity('New recovery codes created', user_id=current_user.id)
    return render_template('account/recovery_codes.html', codes=codes, title='Save your recovery codes')
