import hmac
import secrets
from functools import wraps

from flask import abort, current_app, jsonify, redirect, request, session, url_for


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        expected = session.get("csrf_token", "")
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            if request.path.startswith("/api/") or "/api/" in request.path:
                return jsonify(error="Проверка безопасности не пройдена. Обновите страницу."), 400
            abort(400)
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            if request.path.startswith("/admin/api/"):
                return jsonify(error="Требуется вход ведущего."), 401
            return redirect(url_for("admin.admin_page"))
        return view(*args, **kwargs)

    return wrapped


def check_admin_password(candidate):
    expected = current_app.config["ADMIN_PASSWORD"]
    return bool(candidate and expected and hmac.compare_digest(candidate, expected))

