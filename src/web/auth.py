"""
auth.py
Authentication middleware using Microsoft Entra ID (OIDC).
When AUTH_ENABLED=false (default), all decorators are no-ops.
"""

import logging
import os
from functools import wraps

from flask import redirect, url_for, session, jsonify, request

logger = logging.getLogger(__name__)


def _auth_enabled():
    """Check whether authentication is turned on."""
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def init_auth(app):
    """Configure Flask-Session and identity library when auth is enabled.

    Call this once during app setup. When AUTH_ENABLED is false the function
    is essentially a no-op (only sets a secret key fallback).
    """
    app.secret_key = os.getenv(
        "FLASK_SECRET_KEY", app.config.get("SECRET_KEY", "dev-secret-key")
    )

    if not _auth_enabled():
        logger.info("AUTH_ENABLED=false - all routes are public")
        return

    # Flask-Session with filesystem backend
    from flask_session import Session

    session_dir = os.getenv("SESSION_FILE_DIR", "sessions")
    os.makedirs(session_dir, exist_ok=True)

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = session_dir
    app.config["SESSION_PERMANENT"] = False
    app.config["SESSION_USE_SIGNER"] = True

    Session(app)

    # Configure identity library for Entra ID
    import identity.flask

    tenant_id = os.getenv("AZURE_TENANT_ID", "")
    authority = f"https://login.microsoftonline.com/{tenant_id}" if tenant_id else ""

    app.config["IDENTITY_AUTH"] = {
        "client_id": os.getenv("AZURE_CLIENT_ID", ""),
        "client_credential": os.getenv("AZURE_CLIENT_SECRET", ""),
        "authority": authority,
        "redirect_uri": os.getenv("AZURE_REDIRECT_URI", ""),
    }

    identity.flask.Auth(app)
    logger.info("Entra ID authentication enabled (tenant: %s)", tenant_id)


def get_current_user():
    """Return the current user dict from session, or None."""
    if not _auth_enabled():
        return None
    return session.get("user")


def is_admin(user):
    """Check whether *user* (dict with 'email') is in ADMIN_USERS list."""
    if user is None:
        return False
    admin_csv = os.getenv("ADMIN_USERS", "")
    if not admin_csv:
        return False
    admins = [e.strip().lower() for e in admin_csv.split(",") if e.strip()]
    email = (user.get("email") or user.get("preferred_username") or "").lower()
    return email in admins


# ------------------------------------------------------------------
# Decorators
# ------------------------------------------------------------------


def login_required(func):
    """Redirect unauthenticated browser requests to /login.

    When AUTH_ENABLED=false this is a transparent pass-through.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _auth_enabled():
            return func(*args, **kwargs)
        user = get_current_user()
        if user is None:
            return redirect(url_for("auth.login", next=request.path))
        return func(*args, **kwargs)

    return wrapper


def api_login_required(func):
    """Return 401 JSON for unauthenticated API requests.

    When AUTH_ENABLED=false this is a transparent pass-through.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _auth_enabled():
            return func(*args, **kwargs)
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        return func(*args, **kwargs)

    return wrapper


def admin_required(func):
    """Require admin privileges. Returns 403 for non-admins.

    When AUTH_ENABLED=false this is a transparent pass-through.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _auth_enabled():
            return func(*args, **kwargs)
        user = get_current_user()
        if user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not is_admin(user):
            return jsonify({"error": "Admin access required"}), 403
        return func(*args, **kwargs)

    return wrapper
