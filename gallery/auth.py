"""Shared-password gate for the whole site.

Not per-user auth — a single password (SITE_PASSWORD) shared with whoever
you want to have access, meant for a small closed group on a site that
isn't hosting sensitive data. On success, a signed, long-lived cookie
remembers the device, so the password is entered once per browser rather
than on every visit.

Only wired up when SITE_PASSWORD is configured — see create_app().
"""

import hmac

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)


def is_authed() -> bool:
    return session.get("authed") is True


def safe_next(path: str | None) -> str:
    """Only ever redirect to a same-site relative path — a `next` value
    taken straight from the query string is a classic open-redirect
    vector if it's allowed to point off-site."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def current_path() -> str:
    path = request.path
    if request.query_string:
        path += "?" + request.query_string.decode()
    return path


def _exempt(endpoint: str | None) -> bool:
    if endpoint is None:
        return True  # let unmatched routes 404 normally instead of redirecting
    return endpoint == "static" or endpoint == "gallery.service_worker" or endpoint.startswith("auth.")


def register_gate(app) -> None:
    @app.before_request
    def require_password():
        if _exempt(request.endpoint) or is_authed():
            return None
        return redirect(url_for("auth.login", next=current_path()))


@bp.get("/login")
def login():
    return render_template("login.html", error=None, next=safe_next(request.args.get("next")))


@bp.post("/login")
def login_submit():
    password = request.form.get("password", "")
    next_url = safe_next(request.form.get("next"))
    if hmac.compare_digest(password, current_app.config["SITE_PASSWORD"]):
        session.clear()
        session["authed"] = True
        session.permanent = True
        return redirect(next_url)
    return render_template("login.html", error="Wrong password.", next=next_url), 401


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
