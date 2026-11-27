"""Admin: members."""
import sqlite3
from datetime import datetime
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash
from shop.db import get_db_connection
from shop.forms import AdminResetPasswordForm, AdminUserForm
from shop.security import admin_required, log_activity

from shop.admin import bp


@bp.route('/members')
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
                    current_app.logger.warning(f"Could not parse created_at string: {member_dict['created_at']} for user ID: {member_dict['id']}")
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
        endpoint='admin.admin_members',
        query_params=pagination_query_params
    )


@bp.route('/members/add', methods=['GET', 'POST'])
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
                return redirect(url_for('admin.admin_members'))
            except sqlite3.IntegrityError:
                flash('Username or email already exists.', 'danger')
            except Exception as e:
                current_app.logger.error(f"Error adding member: {e}")
                flash(f'Error adding member: {str(e)}', 'danger')
                log_activity("Admin add member failed", user_id=current_user.id, details=f"Error: {str(e)}")
    return render_template('admin/add_edit_member.html', form=form, title='Add Member', action='Add')


@bp.route('/members/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_member(user_id):
    with get_db_connection() as conn:
        member = conn.execute('SELECT id, username, email, is_admin, confirmed FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not member:
        flash('Member not found.', 'danger')
        return redirect(url_for('admin.admin_members'))

    form = AdminUserForm(obj=member) # Pre-populate
    if form.validate_on_submit() and user_id == current_user.id and not form.is_admin.data:
        flash("You can't remove your own admin rights. Ask another admin to do it.", 'warning')
    elif form.validate_on_submit():
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
            return redirect(url_for('admin.admin_members'))
        except sqlite3.IntegrityError:
            flash('Username or email already exists for another user.', 'danger')
        except Exception as e:
            current_app.logger.error(f"Error updating member {user_id}: {e}")
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


@bp.route('/members/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_member(user_id):
    if user_id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.admin_members'))
    try:
        with get_db_connection() as conn:
            # Orders must keep their customer, so a member with orders can't be deleted
            has_orders = conn.execute("SELECT 1 FROM orders WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
            if has_orders:
                flash('This member has orders and cannot be deleted. Remove their admin rights or reset their password instead.', 'warning')
                return redirect(url_for('admin.admin_members'))
            # Database schema's ON DELETE constraints should handle related data
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        flash('Member deleted successfully.', 'success')
        log_activity("Admin deleted member", user_id=current_user.id, details=f"Deleted User ID: {user_id}")
    except Exception as e:
        current_app.logger.error(f"Error deleting member {user_id}: {e}")
        flash(f'Error deleting member: {str(e)}', 'danger')
        log_activity("Admin delete member failed", user_id=current_user.id, details=f"Deleted User ID: {user_id}, Error: {str(e)}")
    return redirect(url_for('admin.admin_members'))


@bp.route('/members/reset-password/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_reset_user_password(user_id): # Renamed from original if it was admin_reset_password
    form = AdminResetPasswordForm() # AdminResetPasswordForm from app.py (Part 3)
    with get_db_connection() as conn:
        user = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('admin.admin_members'))

    if form.validate_on_submit():
        new_password_hash = generate_password_hash(form.new_password.data)
        try:
            with get_db_connection() as conn:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
                conn.commit()
            flash(f"Password for {user['username']} has been reset.", "success")
            log_activity("Admin reset user password", user_id=current_user.id, details=f"Reset password for User ID: {user_id}")
            return redirect(url_for('admin.admin_edit_member', user_id=user_id)) # Or back to admin_members
        except Exception as e:
            current_app.logger.error(f"Error resetting password for user {user_id}: {e}")
            flash(f"Error resetting password: {str(e)}", "danger")
            log_activity("Admin reset user password failed", user_id=current_user.id, details=f"User ID: {user_id}, Error: {str(e)}")
    return render_template('admin/reset_user_password.html', form=form, user=user, title=f"Reset Password for {user['username']}")
