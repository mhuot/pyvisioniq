"""
auth_routes.py
Blueprint providing /login, /logout, /auth/callback, and /api/auth/status.
When AUTH_ENABLED=false the endpoints still exist but behave as no-ops.
"""

import logging
import os

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    jsonify,
)

from src.web.auth import _auth_enabled, get_current_user, is_admin

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login")
def login():
    """Show login page or redirect to Entra ID."""
    if not _auth_enabled():
        return redirect("/")

    if get_current_user() is not None:
        next_url = request.args.get("next", "/")
        return redirect(next_url)

    return render_template("login.html")


@auth_bp.route("/login/entra")
def login_entra():
    """Initiate Entra ID OIDC flow via the identity library."""
    if not _auth_enabled():
        return redirect("/")

    import identity.flask

    return identity.flask.login()


@auth_bp.route("/auth/callback")
def auth_callback():
    """Handle Entra ID OIDC callback."""
    if not _auth_enabled():
        return redirect("/")

    import identity.flask

    result = identity.flask.complete_login()
    if result is None:
        return redirect("/login?error=auth_failed")

    # Store user info in session
    session["user"] = {
        "name": result.get("name", ""),
        "email": result.get("preferred_username", ""),
        "preferred_username": result.get("preferred_username", ""),
        "oid": result.get("oid", ""),
    }
    logger.info("User logged in: %s", session["user"].get("email"))

    next_url = session.pop("login_next", "/")
    return redirect(next_url)


@auth_bp.route("/logout")
def logout():
    """Clear session and redirect home."""
    user_email = (get_current_user() or {}).get("email", "unknown")
    session.clear()
    logger.info("User logged out: %s", user_email)

    if _auth_enabled():
        tenant_id = os.getenv("AZURE_TENANT_ID", "")
        if tenant_id:
            logout_url = (
                f"https://login.microsoftonline.com/{tenant_id}"
                f"/oauth2/v2.0/logout?post_logout_redirect_uri="
                f"{request.host_url}"
            )
            return redirect(logout_url)

    return redirect("/")


@auth_bp.route("/api/auth/status")
def auth_status():
    """Return current authentication state as JSON."""
    if not _auth_enabled():
        return jsonify(
            {
                "auth_enabled": False,
                "authenticated": False,
                "user": None,
                "is_admin": False,
            }
        )

    user = get_current_user()
    if user is None:
        return jsonify(
            {
                "auth_enabled": True,
                "authenticated": False,
                "user": None,
                "is_admin": False,
            }
        )

    return jsonify(
        {
            "auth_enabled": True,
            "authenticated": True,
            "user": {"name": user.get("name", ""), "email": user.get("email", "")},
            "is_admin": is_admin(user),
        }
    )
