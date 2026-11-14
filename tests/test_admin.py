from tests.conftest import admin_action, admin_login


def test_admin_actions_require_login_and_csrf(client):
    assert client.get("/admin/api/state").status_code == 401
    assert client.post("/admin/api/action/start").status_code == 401
    admin_login(client)
    assert client.post("/admin/api/action/start", json={}).status_code == 400
    assert admin_action(client, "start").status_code == 200


def test_wrong_admin_password(client):
    client.get("/admin")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post("/admin/login", data={"csrf_token": token, "password": "wrong"})
    assert response.status_code == 401


def test_reset_starts_clean_round_and_applies_confirmation(client):
    admin_login(client)
    assert admin_action(client, "start").status_code == 200
    assert admin_action(client, "reset", {"confirmation": "wrong"}).status_code == 409
    assert admin_action(client, "reset", {"confirmation": "ОЧИСТИТЬ"}).status_code == 200
    state = client.get("/admin/api/state").get_json()
    assert state["round"]["status"] == "waiting"
    assert state["participants"] == []


def test_csv_escapes_excel_formula_names(app):
    host = app.test_client()
    admin_login(host)
    admin_action(host, "open")
    player = app.test_client()
    from tests.conftest import join
    join(player, "=2+2")
    response = host.get("/admin/results.csv")
    assert response.status_code == 200
    assert "'=2+2" in response.data.decode("utf-8-sig")


def test_numeric_answer_is_not_reported_as_missed(app):
    host = app.test_client()
    admin_login(host)
    assert admin_action(host, "start").status_code == 200
    player = app.test_client()
    from tests.conftest import answer_current, join
    join(player, "Числовой ответ")
    assert answer_current(player, answer="2210").status_code == 200
    state = host.get("/admin/api/state").get_json()
    assert state["participants"][0]["answered_count"] == 1
    assert state["participants"][0]["missed_count"] == 0

