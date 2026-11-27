"""Entry point: `flask --app app run` (development) or `gunicorn app:app` (production)."""
import os

from shop import create_app

app = create_app()

if __name__ == '__main__':
    # Debug mode exposes an interactive console - only enable it locally: FLASK_DEBUG=1
    app.run(debug=os.environ.get('FLASK_DEBUG', '0') == '1',
            host=os.environ.get('FLASK_HOST') or '127.0.0.1',
            port=int(os.environ.get('PORT') or 5000))
