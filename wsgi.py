"""
WSGI entry point for production deployment.

Development uses `python app.py` (the Werkzeug dev server). Production
should use a real WSGI server instead — gunicorn is what this project's
own README recommends:

    gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app2

This file exists as a conventional, explicit entry point separate from
app.py so:
  - the WSGI server target is always `wsgi:app`, regardless of internal
    refactors to app.py's module-level code, and
  - it's obvious at a glance (e.g. in a Procfile or systemd unit) that this
    process is the production entry point, not the dev server.

`app.py` already creates the Flask `app` object and calls db.init_db() at
import time (not inside its `if __name__ == "__main__":` guard), so simply
importing it here is enough — no extra setup is needed for gunicorn's
worker processes to get a working, migrated database.
"""
from app import app

if __name__ == "__main__":
    # Allows `python wsgi.py` as a quick sanity check that the import path
    # itself works, but this is not how it should be run in production —
    # use gunicorn (see module docstring) so you get multiple workers,
    # proper request timeouts, and no Werkzeug dev-server warnings.
    app.run()
