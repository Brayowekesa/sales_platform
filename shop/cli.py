"""Command-line tasks: flask --app app init-db / create-admin."""
import sqlite3
import click
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash
from shop.db import get_db_connection, init_db
from shop.security import log_activity


@click.command('init-db')
@with_appcontext
def init_db_command():
    """Initializes the database by creating tables."""
    print("Attempting to initialize database...") # Add print statement
    try:
        init_db() # Your function from Part 1
        print("Database initialization process completed.")
    except Exception as e:
        print(f"An error occurred during init_db: {e}")


@click.command('create-admin')
@with_appcontext
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
