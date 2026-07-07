"""Gallery website entry point.

Development:  .venv/bin/python3 wsgi.py            (debug + auto-reload)
Production:   gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app

PORT (dev server only) and the app's PHOTOS_DIR / MONGO_URI / MONGO_DB are
read from the environment or the .env file — see gallery/__init__.py.
"""

import os

from gallery import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), debug=True)
