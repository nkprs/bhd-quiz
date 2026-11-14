import os
from pathlib import Path

from flask import Flask

from . import admin, public
from .db import connect_database, init_app as init_db_app, init_schema
from .game import ensure_current_round
from .security import csrf_token


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def create_app(test_config=None):
    project_root = Path(__file__).resolve().parent.parent
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD"),
        DATABASE=os.environ.get("QUIZ_DATABASE", str(project_root / "instance" / "quiz.sqlite")),
        QUIZ_CONFIG=os.environ.get("QUIZ_CONFIG", str(project_root / "config" / "quiz.json")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_as_bool(os.environ.get("SESSION_COOKIE_SECURE"), False),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-only-secret"
        else:
            raise RuntimeError("Задайте переменную окружения FLASK_SECRET_KEY")
    if not app.config.get("ADMIN_PASSWORD"):
        if app.config.get("TESTING"):
            app.config["ADMIN_PASSWORD"] = "test-password"
        else:
            raise RuntimeError("Задайте переменную окружения ADMIN_PASSWORD")

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database(app.config["DATABASE"])
    try:
        init_schema(connection)
        with app.app_context():
            ensure_current_round(connection)
    finally:
        connection.close()

    init_db_app(app)
    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        return response

    return app

