"""Sentinel application factory. The connected device is explicitly a simulator."""

import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import generate_password_hash

import config
from . import db


def local_secret(instance_path):
    path = Path(instance_path) / "secret.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_text(encoding="utf-8").strip()
    else:
        key = secrets.token_hex(32)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key)
    if len(key) < 32:
        raise RuntimeError(
            "instance/secret.key is invalid. Supply a SECRET_KEY of at least 32 characters."
        )
    return key


def create_app(test_config=None):
    overrides = test_config or {}
    instance = str(Path(overrides.get("INSTANCE_PATH", config.BASE_DIR / "instance")).resolve())
    app = Flask(
        __name__, instance_path=instance, template_folder="../templates", static_folder="../static"
    )
    database = Path(config.SQLITE_DATABASE)
    if not database.is_absolute():
        database = config.BASE_DIR / database
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        DATABASE=str(database),
        SEED_DEMO=config.SEED_DEMO,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.SECURE_COOKIES,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        MAX_CONTENT_LENGTH=64 * 1024,
        WTF_CSRF_TIME_LIMIT=7200,
        EVENTS_PER_PAGE=10,
    )
    app.config.update(overrides)
    Path(instance).mkdir(parents=True, exist_ok=True)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = local_secret(instance)
    elif len(app.config["SECRET_KEY"]) < 32 or "replace-me" in app.config["SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY must be a random string of at least 32 characters; omit it for the local demo."
        )
    app.config["DUMMY_HASH"] = generate_password_hash(secrets.token_hex(24))
    CSRFProtect(app)
    app.teardown_appcontext(db.close_db)
    with app.app_context():
        db.initialize()

    from .routes import bp, register_cli

    app.register_blueprint(bp)
    register_cli(app)

    @app.after_request
    def response_headers(response):
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                "Cache-Control": "no-store",
            }
        )
        return response

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        message = "Your session expired or this request is invalid. Refresh the page and try again."
        if request.path.startswith("/api/"):
            return jsonify(error=message), 400
        return render_template("error.html", title="Request expired", message=message), 400

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.exception("Database operation failed")
        if request.path.startswith("/api/"):
            return jsonify(error="The request could not be saved. Try again."), 503
        return render_template(
            "error.html", title="Unable to save", message="Please try again shortly."
        ), 503

    def http_error(error):
        if request.path.startswith("/api/"):
            return jsonify(error=error.description), error.code
        return render_template(
            "error.html", title=error.name, message=error.description
        ), error.code

    for code in (400, 403, 404, 405, 413):
        app.register_error_handler(code, http_error)
    return app
