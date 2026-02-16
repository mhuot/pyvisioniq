"""
auth_routes.py
Blueprint providing /login and /api/auth/status.
The identity library auto-registers /auth/callback and /auth/logout.
When AUTH_ENABLED=false the endpoints still exist but behave as no-ops.
"""

import logging

from flask import Blueprint, jsonify, redirect, render_template, request

from src.web.auth import _auth_enabled, get_current_user, get_identity_auth, is_admin

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

    auth = get_identity_auth()
    if auth is None:
        return redirect("/login?error=not_configured")

    next_url = request.args.get("next", "/")
    return auth.login(next_link=next_url)


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
