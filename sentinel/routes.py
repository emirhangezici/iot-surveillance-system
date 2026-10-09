"""Authentication, transactional simulator controls, event history and accounts."""

import csv
import io
import math
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import click
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, now, record_event

bp = Blueprint("web", __name__)


@bp.before_app_request
def load_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user and user["active"] and user["auth_version"] == session.get("auth_version"):
            g.user = user
        else:
            session.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="Please sign in again."), 401
            return redirect(url_for("web.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def throttled(username):
    conn = get_db()
    cutoff = int(time.time()) - 300
    with conn:
        conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))
    return (
        conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE attempted_at >= ? AND (username = ? OR address = ?)",
            (cutoff, username.lower(), request.remote_addr or "local"),
        ).fetchone()[0]
        >= 10
    )


def failed_login(username):
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO login_attempts (username, address, attempted_at) VALUES (?, ?, ?)",
            (username.lower(), request.remote_addr or "local", int(time.time())),
        )


@bp.route("/", methods=["GET", "POST"])
@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("web.dashboard"))
    username = ""
    error = None
    status = 200
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if throttled(username):
            error, status = "Too many sign-in attempts. Try again in five minutes.", 429
        else:
            user = (
                get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                if len(username) <= 32
                else None
            )
            valid = len(password) <= 128 and check_password_hash(
                user["password_hash"] if user else current_app.config["DUMMY_HASH"], password
            )
            if valid and user and user["active"]:
                session.clear()
                session.update(user_id=user["id"], auth_version=user["auth_version"])
                session.permanent = True
                return redirect(url_for("web.dashboard"))
            failed_login(username)
            error, status = "Invalid username or password.", 401
    return render_template("login.html", username=username, error=error), status


@bp.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("web.login"))


def state_payload(conn=None):
    conn = conn or get_db()
    row = conn.execute("SELECT * FROM device WHERE id = 1").fetchone()
    state = (
        "Unknown"
        if not row["online"]
        else "Triggered"
        if row["triggered"]
        else "Armed"
        if row["armed"]
        else "Disarmed"
    )
    alerts = conn.execute(
        "SELECT COUNT(*) FROM events WHERE needs_review = 1 AND acknowledged_at IS NULL"
    ).fetchone()[0]
    return {
        "state": state,
        "armed": bool(row["armed"]),
        "triggered": bool(row["triggered"]),
        "online": bool(row["online"]),
        "revision": row["revision"],
        "updated_at": row["updated_at"],
        "open_alerts": alerts,
        "mode": "simulator",
    }


@bp.get("/dashboard", endpoint="dashboard")
@bp.get("/events", endpoint="event_history")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        state=state_payload(),
        view="events" if request.path == "/events" else "overview",
    )


@bp.get("/api/state")
@login_required
def state():
    return jsonify(state_payload())


def device_request():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or type(body.get("revision")) is not int:
        return None, None, (jsonify(error="A valid device revision is required."), 400)
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM device WHERE id = 1").fetchone()
    if row["revision"] != body["revision"]:
        conn.rollback()
        return (
            None,
            None,
            (
                jsonify(
                    error="The device changed in another session. Review its current state and try again.",
                    state=state_payload(),
                ),
                409,
            ),
        )
    return body, row, None


@bp.post("/api/control")
@login_required
def control():
    body, row, error = device_request()
    if error:
        return error
    conn = get_db()
    try:
        action = body.get("action")
        if not isinstance(action, str) or action not in {"arm", "disarm"}:
            return jsonify(error="Choose arm or disarm."), 400
        if not row["online"]:
            return jsonify(error="The device is offline. Its alarm state cannot be confirmed."), 409
        armed = action == "arm"
        if bool(row["armed"]) != armed:
            conn.execute(
                "UPDATE device SET armed = ?, triggered = 0, revision = revision + 1, updated_at = ? WHERE id = 1",
                (int(armed), now()),
            )
            record_event(conn, "Alarm armed" if armed else "Alarm disarmed", "control", "Operator")
        conn.commit()
        return jsonify(
            state=state_payload(),
            message="Simulator confirmed: " + ("armed." if armed else "disarmed."),
        )
    finally:
        if conn.in_transaction:
            conn.rollback()


@bp.post("/api/simulate")
@login_required
def simulate():
    body, row, error = device_request()
    if error:
        return error
    conn = get_db()
    try:
        event = body.get("event")
        if not isinstance(event, str) or event not in {"motion", "disconnect", "reconnect"}:
            return jsonify(error="Invalid simulator event."), 400
        if event == "motion":
            if not row["online"]:
                return jsonify(error="Reconnect the simulator before sending motion."), 409
            conn.execute(
                "UPDATE device SET triggered = ?, revision = revision + 1, updated_at = ? WHERE id = 1",
                (row["armed"], now()),
            )
            record_event(conn, "Motion detected", "motion", review=bool(row["armed"]))
            message = (
                "Simulated motion triggered the alarm."
                if row["armed"]
                else "Motion recorded while the alarm was disarmed."
            )
        else:
            online = event == "reconnect"
            if bool(row["online"]) != online:
                conn.execute(
                    "UPDATE device SET online = ?, revision = revision + 1, updated_at = ? WHERE id = 1",
                    (int(online), now()),
                )
                record_event(
                    conn, "Device reconnected" if online else "Device disconnected", "device"
                )
            message = (
                "Simulator reconnected."
                if online
                else "Device offline. Last confirmed alarm state is preserved."
            )
        conn.commit()
        return jsonify(state=state_payload(), message=message)
    finally:
        if conn.in_transaction:
            conn.rollback()


@bp.post("/api/events/acknowledge")
@login_required
def acknowledge():
    body, row, error = device_request()
    if error:
        return error
    conn = get_db()
    try:
        count = conn.execute(
            "UPDATE events SET acknowledged_at = ?, acknowledged_by = ? WHERE needs_review = 1 AND acknowledged_at IS NULL",
            (now(), g.user["username"]),
        ).rowcount
        if count:
            conn.execute(
                "UPDATE device SET triggered = 0, revision = revision + 1, updated_at = ? WHERE id = 1",
                (now(),),
            )
            record_event(conn, "Alerts acknowledged", "control", "Operator")
        conn.commit()
        return jsonify(
            state=state_payload(),
            message=f"Acknowledged {count} alert(s). Event history is preserved.",
        )
    finally:
        if conn.in_transaction:
            conn.rollback()


def event_filters():
    kind = request.args.get("kind", "all")
    period = request.args.get("period", "all")
    query = request.args.get("q", "").strip()
    if (
        kind not in {"all", "motion", "control", "device", "account"}
        or period not in {"all", "day", "week"}
        or len(query) > 100
    ):
        abort(400, description="Invalid event filters.")
    clauses, values = [], []
    if kind != "all":
        clauses.append("kind = ?")
        values.append(kind)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(
            "(label LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\' OR actor_name LIKE ? ESCAPE '\\')"
        )
        values.extend(["%" + escaped + "%"] * 3)
    if period != "all":
        cutoff = datetime.now(timezone.utc) - timedelta(days=1 if period == "day" else 7)
        clauses.append("created_at >= ?")
        values.append(cutoff.isoformat(timespec="milliseconds"))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", values


@bp.get("/api/events")
@login_required
def events():
    where, values = event_filters()
    raw_page = request.args.get("page", "1")
    if not re.fullmatch(r"[1-9][0-9]{0,5}", raw_page) or int(raw_page) > 100000:
        abort(400, description="Invalid page number.")
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM events" + where, values).fetchone()[0]
    per_page = current_app.config["EVENTS_PER_PAGE"]
    pages = max(1, math.ceil(total / per_page))
    page = min(int(raw_page), pages)
    rows = conn.execute(
        "SELECT * FROM events" + where + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        [*values, per_page, (page - 1) * per_page],
    ).fetchall()
    return jsonify(events=[dict(row) for row in rows], total=total, page=page, pages=pages)


def csv_safe(value):
    value = str(value if value is not None else "")
    return (
        "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value
    )


@bp.get("/events/export")
@login_required
def export():
    where, values = event_filters()
    rows = (
        get_db()
        .execute(
            "SELECT * FROM events" + where + " ORDER BY created_at DESC, id DESC LIMIT 5000", values
        )
        .fetchall()
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["ID", "Timestamp (UTC)", "Type", "Event", "Source", "Actor", "Status", "Acknowledged by"]
    )
    for row in rows:
        status = (
            "Reviewed"
            if row["acknowledged_at"]
            else "Needs review"
            if row["needs_review"]
            else "Recorded"
        )
        writer.writerow(
            [
                csv_safe(v)
                for v in (
                    row["id"],
                    row["created_at"],
                    row["kind"],
                    row["label"],
                    row["source"],
                    row["actor_name"],
                    status,
                    row["acknowledged_by"],
                )
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="sentinel-events.csv"',
            "X-Export-Limit": "5000",
        },
    )


def validate_account(username, password, role):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}", username):
        return "Use a 3–32 character username starting with a letter or number. Allowed: letters, numbers, dots, underscores, hyphens."
    if not 10 <= len(password) <= 128:
        return "Use a password between 10 and 128 characters."
    if role not in {"admin", "operator"}:
        return "Choose a valid account role."
    return None


@bp.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    username, role = "", "operator"
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        error = validate_account(username, password, role)
        if not error:
            conn = get_db()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                        (username, generate_password_hash(password), role, now()),
                    )
                    record_event(conn, "Account created: " + username, "account", "Administration")
                flash("Account created.", "success")
                return redirect(url_for("web.admin"))
            except sqlite3.IntegrityError:
                error = "That username is already taken."
    users = (
        get_db()
        .execute("SELECT id, username, role, active, created_at FROM users ORDER BY username")
        .fetchall()
    )
    return render_template(
        "admin.html", users=users, error=error, username=username, role=role
    ), 400 if error else 200


@bp.post("/admin/users/<int:user_id>/toggle")
@admin_required
def toggle_user(user_id):
    if user_id == g.user["id"]:
        abort(400, description="You cannot disable your own account.")
    conn = get_db()
    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            abort(404)
        conn.execute(
            "UPDATE users SET active = ?, auth_version = auth_version + 1 WHERE id = ?",
            (int(not user["active"]), user_id),
        )
        record_event(
            conn,
            ("Account disabled: " if user["active"] else "Account enabled: ") + user["username"],
            "account",
            "Administration",
        )
    flash("Account access updated. Existing sessions have been revoked.", "success")
    return redirect(url_for("web.admin"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    error = None
    if request.method == "POST":
        old = request.form.get("current_password", "")
        password = request.form.get("new_password", "")
        if throttled(g.user["username"]):
            return render_template(
                "account.html", error="Too many attempts. Try again in five minutes."
            ), 429
        if len(old) > 128 or not check_password_hash(g.user["password_hash"], old):
            failed_login(g.user["username"])
            error = "Your current password is incorrect."
        elif not 10 <= len(password) <= 128:
            error = "Use a password between 10 and 128 characters."
        elif password != request.form.get("confirm_password", ""):
            error = "The new passwords do not match."
        else:
            conn = get_db()
            with conn:
                conn.execute(
                    "UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE id = ?",
                    (generate_password_hash(password), g.user["id"]),
                )
                record_event(conn, "Password changed", "account", "Account settings")
            session.clear()
            flash("Password changed. Sign in again with your new password.", "success")
            return redirect(url_for("web.login"))
    return render_template("account.html", error=error), 400 if error else 200


def register_cli(app):
    @app.cli.command("create-admin")
    @click.argument("username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(username, password):
        """Create an admin without enabling demo credentials."""
        error = validate_account(username, password, "admin")
        if error:
            raise click.ClickException(error)
        conn = get_db()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                    (username, generate_password_hash(password), now()),
                )
        except sqlite3.IntegrityError:
            raise click.ClickException("That username already exists.") from None
        click.echo("Admin account created.")
