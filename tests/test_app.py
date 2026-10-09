import csv
import io
import re

import pytest
from werkzeug.security import check_password_hash

from sentinel import create_app
from sentinel.db import get_db


@pytest.fixture
def settings(tmp_path):
    return {
        "TESTING": True,
        "SECRET_KEY": "test-key-" * 8,
        "INSTANCE_PATH": str(tmp_path / "instance"),
        "DATABASE": str(tmp_path / "demo.sqlite3"),
        "SEED_DEMO": True,
    }


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return app.test_client()


def token(client, path="/login"):
    html = client.get(path, follow_redirects=True).get_data(as_text=True)
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)


def login(client, username="admin", password="DemoAdmin!2026"):
    return client.post(
        "/login", data={"username": username, "password": password, "csrf_token": token(client)}
    )


def action(client, route, **body):
    if "revision" not in body:
        body["revision"] = client.get("/api/state").json["revision"]
    return client.post(route, json=body, headers={"X-CSRFToken": token(client, "/dashboard")})


def test_fresh_start_hashes_and_idempotent_bootstrap(app, settings):
    with app.app_context():
        users = get_db().execute("SELECT * FROM users").fetchall()
        assert len(users) == 2
        assert users[0]["password_hash"] != "DemoAdmin!2026"
        assert check_password_hash(users[0]["password_hash"], "DemoAdmin!2026")
    second = create_app(settings)
    with second.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert get_db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_generated_secret_persists_and_sessions_survive_restart(settings):
    settings["SECRET_KEY"] = None
    first = create_app(settings)
    client = first.test_client()
    login(client)
    cookie = client.get_cookie("session")
    second = create_app(settings)
    assert second.secret_key == first.secret_key
    other = second.test_client()
    other.set_cookie("session", cookie.value)
    assert other.get("/api/state").status_code == 200


def test_access_database_is_not_overwritten(settings, tmp_path):
    file = tmp_path / "legacy.accdb"
    file.write_bytes(b"original Access data")
    settings["DATABASE"] = str(file)
    with pytest.raises(RuntimeError, match="not an Access"):
        create_app(settings)
    assert file.read_bytes() == b"original Access data"


def test_login_logout_and_access_boundaries(app, client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/events/export").status_code == 302
    response = login(client, "operator", "DemoUser!2026")
    assert response.status_code == 302 and response.location.endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    assert client.get("/admin").status_code == 403
    assert client.get("/logout").status_code == 405
    assert client.post("/logout", data={"csrf_token": token(client)}).status_code == 302
    assert client.get("/api/state").status_code == 401


def test_csrf_rejects_login_and_mutation(client):
    assert (
        client.post("/login", data={"username": "admin", "password": "DemoAdmin!2026"}).status_code
        == 400
    )
    login(client)
    before = client.get("/api/state").json
    response = client.post("/api/control", json={"action": "arm", "revision": before["revision"]})
    assert response.status_code == 400
    assert client.get("/api/state").json == before


def test_login_throttle(client):
    for _ in range(10):
        assert login(client, password="wrong-password").status_code == 401
    response = login(client)
    assert response.status_code == 429
    assert b"five minutes" in response.data


def test_complete_alarm_workflow_and_acknowledgement(client):
    login(client)
    assert client.get("/api/state").json["state"] == "Disarmed"
    assert action(client, "/api/simulate", event="motion").json["state"]["state"] == "Disarmed"
    assert action(client, "/api/control", action="arm").json["state"]["state"] == "Armed"
    triggered = action(client, "/api/simulate", event="motion").json["state"]
    assert triggered["state"] == "Triggered" and triggered["open_alerts"] == 1
    before = client.get("/api/events").json["total"]
    ack = action(client, "/api/events/acknowledge").json["state"]
    assert ack["state"] == "Armed" and ack["open_alerts"] == 0
    assert client.get("/api/events").json["total"] == before + 1
    motion = client.get("/api/events?kind=motion").json["events"]
    assert motion[0]["acknowledged_by"] == "admin"
    assert action(client, "/api/control", action="disarm").json["state"]["state"] == "Disarmed"


def test_offline_preserves_state_and_blocks_control(client, settings):
    login(client)
    action(client, "/api/control", action="arm")
    offline = action(client, "/api/simulate", event="disconnect").json["state"]
    assert offline["state"] == "Unknown" and offline["armed"] is True
    before = client.get("/api/events").json["total"]
    assert action(client, "/api/control", action="disarm").status_code == 409
    assert action(client, "/api/simulate", event="motion").status_code == 409
    assert client.get("/api/events").json["total"] == before
    second = create_app(settings).test_client()
    login(second)
    assert second.get("/api/state").json["state"] == "Unknown"
    assert action(second, "/api/simulate", event="reconnect").json["state"]["state"] == "Armed"


def test_stale_client_cannot_override_newer_state(app, client):
    login(client)
    stale = client.get("/api/state").json["revision"]
    other = app.test_client()
    login(other, "operator", "DemoUser!2026")
    action(other, "/api/control", action="arm")
    response = action(client, "/api/control", action="disarm", revision=stale)
    assert response.status_code == 409 and response.json["state"]["state"] == "Armed"
    assert client.get("/api/state").json["state"] == "Armed"


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"revision": True},
        {"revision": 0, "action": []},
        {"revision": 0, "action": "invalid"},
    ],
)
def test_bad_control_payload_is_rejected_without_side_effects(client, body):
    login(client)
    response = client.post("/api/control", json=body, headers={"X-CSRFToken": token(client)})
    assert response.status_code == 400
    assert client.get("/api/state").json["state"] == "Disarmed"
    assert action(client, "/api/control", action="arm").status_code == 200


def test_repeated_command_does_not_duplicate_event(client):
    login(client)
    action(client, "/api/control", action="arm")
    count = client.get("/api/events").json["total"]
    action(client, "/api/control", action="arm")
    assert client.get("/api/events").json["total"] == count


def test_filters_pagination_and_csv_export(client, app):
    login(client)
    for _ in range(13):
        action(client, "/api/simulate", event="motion")
    page = client.get("/api/events?kind=motion").json
    assert page["total"] == 13 and page["pages"] == 2 and len(page["events"]) == 10
    assert len(client.get("/api/events?kind=motion&page=2").json["events"]) == 3
    assert client.get("/api/events?q=nothingmatches").json["total"] == 0
    assert client.get("/api/events?q=%25").json["total"] == 0
    assert client.get("/api/events?kind=motion&period=day").json["total"] == 13
    for query in ("page=0", "page=wat", "kind=evil", "period=bad", "page=" + "9" * 5000):
        response = client.get("/api/events?" + query)
        assert response.status_code == 400 and response.is_json
    with app.app_context():
        conn = get_db()
        with conn:
            conn.execute("UPDATE events SET source = '=1+1' WHERE kind = 'motion'")
    response = client.get("/events/export?kind=motion")
    rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
    assert len(rows) == 14 and rows[1][4] == "'=1+1"
    assert response.headers["X-Export-Limit"] == "5000"


def test_admin_validation_and_disabled_session_revocation(app, client):
    login(client)
    csrf = token(client)
    for username, role, password in (
        ("bad name", "operator", "GoodPassword!123"),
        ("admin", "operator", "GoodPassword!123"),
        ("test", "superadmin", "GoodPassword!123"),
        ("test", "operator", "short"),
    ):
        assert (
            client.post(
                "/admin",
                data={"csrf_token": csrf, "username": username, "role": role, "password": password},
            ).status_code
            == 400
        )
    assert (
        client.post(
            "/admin",
            data={
                "csrf_token": csrf,
                "username": "tester",
                "role": "operator",
                "password": "GoodPassword!123",
            },
        ).status_code
        == 302
    )
    operator = app.test_client()
    login(operator, "operator", "DemoUser!2026")
    assert (
        operator.post(
            "/admin",
            data={
                "csrf_token": token(operator),
                "username": "evil",
                "role": "admin",
                "password": "GoodPassword!123",
            },
        ).status_code
        == 403
    )
    assert client.post("/admin/users/1/toggle", data={"csrf_token": csrf}).status_code == 400
    assert client.post("/admin/users/2/toggle", data={"csrf_token": csrf}).status_code == 302
    assert operator.get("/api/state").status_code == 401
    assert login(operator, "operator", "DemoUser!2026").status_code == 401
    assert client.post("/admin/users/2/toggle", data={"csrf_token": csrf}).status_code == 302
    assert login(operator, "operator", "DemoUser!2026").status_code == 302


def test_password_change_revokes_other_sessions(app, client):
    login(client)
    other = app.test_client()
    login(other)
    response = client.post(
        "/account",
        data={
            "csrf_token": token(client),
            "current_password": "DemoAdmin!2026",
            "new_password": "ChangedPassword!123",
            "confirm_password": "ChangedPassword!123",
        },
    )
    assert response.status_code == 302
    assert other.get("/api/state").status_code == 401
    assert login(client, password="DemoAdmin!2026").status_code == 401
    assert login(client, password="ChangedPassword!123").status_code == 302


def test_events_cannot_be_deleted(client):
    login(client)
    before = client.get("/api/events").json["total"]
    assert (
        client.post("/delete_log", data={"csrf_token": token(client), "log_id": 1}).status_code
        == 404
    )
    assert client.get("/api/events").json["total"] == before


def test_seed_disabled_and_cli_admin(settings):
    settings["SEED_DEMO"] = False
    app = create_app(settings)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    result = app.test_cli_runner().invoke(
        args=["create-admin", "owner"], input="StrongPassword!123\nStrongPassword!123\n"
    )
    assert result.exit_code == 0
    assert login(app.test_client(), "owner", "StrongPassword!123").status_code == 302


def test_response_security_headers(client):
    response = client.get("/login")
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_overlong_username_is_not_truncated_into_a_real_account(client):
    login(client)
    name = "x" * 32
    assert (
        client.post(
            "/admin",
            data={
                "csrf_token": token(client),
                "username": name,
                "password": "LongNamePassword!123",
                "role": "operator",
            },
        ).status_code
        == 302
    )
    client.post("/logout", data={"csrf_token": token(client)})
    assert login(client, name + "extra", "LongNamePassword!123").status_code == 401
    assert login(client, name, "LongNamePassword!123").status_code == 302
