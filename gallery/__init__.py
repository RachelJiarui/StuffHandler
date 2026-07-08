"""Gallery web app (Flask).

Build an instance with create_app(); wsgi.py at the repo root does exactly
that and is the entry point for both dev (`python wsgi.py`) and production
(`gunicorn wsgi:app`).

Configuration comes from environment variables (a .env file at the repo
root is loaded automatically):

    PHOTOS_DIR    folder of images to display     (default: <repo>/done_output)
    GCP_PROJECT   GCP project holding Firestore    (default: ADC's default project)

Data lives in Firestore (collections `items`, `uploads`), authenticated via
Application Default Credentials — `gcloud auth application-default login`
locally, or the VM's attached service account when deployed.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from google.cloud import firestore

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(overrides: dict | None = None) -> Flask:
    load_dotenv(PROJECT_ROOT / ".env")

    app = Flask(__name__)
    app.config.from_mapping(
        PHOTOS_DIR=os.environ.get("PHOTOS_DIR", PROJECT_ROOT / "done_output"),
        UPLOADS_DIR=os.environ.get("UPLOADS_DIR", PROJECT_ROOT / "uploads"),
        GCP_PROJECT=os.environ.get("GCP_PROJECT", ""),
        UPLOAD_WORKERS=int(os.environ.get("UPLOAD_WORKERS", "3")),
        SITE_PASSWORD=os.environ.get("SITE_PASSWORD", ""),
        SECRET_KEY=os.environ.get("SECRET_KEY", ""),
    )
    if overrides:
        app.config.update(overrides)
    app.config["PHOTOS_DIR"] = Path(app.config["PHOTOS_DIR"]).resolve()
    app.config["UPLOADS_DIR"] = Path(app.config["UPLOADS_DIR"]).resolve()

    photos_dir = app.config["PHOTOS_DIR"]
    if not photos_dir.is_dir():
        raise SystemExit(
            f"Error: photos directory '{photos_dir}' does not exist "
            "(set PHOTOS_DIR to the folder of images to display)."
        )

    try:
        db = firestore.Client(project=app.config["GCP_PROJECT"] or None)
        # Cheap connectivity/credentials check, mirroring the old Mongo ping.
        next(db.collection("items").limit(1).stream(), None)
    except Exception as e:
        raise SystemExit(
            f"Error: couldn't reach Firestore ({e}).\n"
            "Locally: gcloud auth application-default login\n"
            "Deployed: the VM's service account needs the 'Cloud Datastore "
            "User' IAM role, and GCP_PROJECT must name the right project."
        )
    app.extensions["gallery_firestore"] = db
    app.extensions["gallery_items"] = db.collection("items")

    uploads_dir = app.config["UPLOADS_DIR"]
    (uploads_dir / "originals").mkdir(parents=True, exist_ok=True)
    (uploads_dir / "processed").mkdir(parents=True, exist_ok=True)

    from .processing import UploadProcessor, mark_stale_jobs

    uploads_coll = db.collection("uploads")
    mark_stale_jobs(db, uploads_coll)
    app.extensions["gallery_uploads"] = uploads_coll
    app.extensions["gallery_processor"] = UploadProcessor(
        db, uploads_coll, uploads_dir / "originals", uploads_dir / "processed",
        workers=app.config["UPLOAD_WORKERS"],
    )

    from . import routes, uploads
    from .notes import notes_to_html

    app.register_blueprint(routes.bp)
    app.register_blueprint(uploads.bp)
    app.jinja_env.filters["notes_html"] = notes_to_html
    app.jinja_env.globals["site_title"] = "Stuff Handler"
    app.jinja_env.globals["auth_enabled"] = False

    # Shared-password gate — only active when configured, so local/LAN-only
    # use (no exposure beyond your own network) stays frictionless.
    if app.config["SITE_PASSWORD"]:
        if not app.config["SECRET_KEY"]:
            raise SystemExit(
                "Error: SITE_PASSWORD is set but SECRET_KEY is not. Both are "
                "required together — SECRET_KEY signs the remember-this-"
                "device cookie, and must stay the same across restarts and "
                "worker processes or sessions will randomly invalidate.\n"
                "Generate one with:\n"
                '  python3 -c "import secrets; print(secrets.token_hex(32))"'
            )
        app.secret_key = app.config["SECRET_KEY"]
        app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        # Cookie requires HTTPS by default (matches the deployed setup);
        # set COOKIE_SECURE=false only to test the login flow over plain
        # HTTP on localhost/LAN.
        app.config["SESSION_COOKIE_SECURE"] = (
            os.environ.get("COOKIE_SECURE", "true").lower() != "false"
        )

        from . import auth

        app.register_blueprint(auth.bp)
        auth.register_gate(app)
        app.jinja_env.globals["auth_enabled"] = True

    return app
