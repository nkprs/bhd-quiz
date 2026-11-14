from concurrent.futures import ThreadPoolExecutor
import time

from app.db import connect_database
from tests.conftest import admin_action, admin_login, answer_current, join


def start_quiz(client):
    admin_login(client)
    assert admin_action(client, "start").status_code == 200


def test_complete_ten_questions_and_restore_progress(app):
    host = app.test_client()
    start_quiz(host)
    player = app.test_client()
    join(player, "Мама")

    for index in range(10):
        response = answer_current(player, "a")
        assert response.status_code == 200
        if index < 9:
            assert admin_action(host, "next").status_code == 200
            restored = player.get("/api/quiz/state").get_json()
            assert restored["question_index"] == index + 1
    assert admin_action(host, "next").status_code == 200

    state = player.get("/api/quiz/state").get_json()
    assert state["round_status"] == "finished"
    assert state["result"] == {"score": 16, "total": 16}
    ranking = player.get("/api/ranking").get_json()["ranking"]
    assert ranking[0]["name"] == "Мама"
    assert ranking[0]["score"] == 16


def test_duplicate_answer_is_idempotent(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player)
    state = player.get("/api/quiz/state").get_json()
    with player.session_transaction() as session:
        token = session["csrf_token"]
    payload = {"question_id": state["question"]["id"], "answer": "22:10"}
    first = player.post("/api/answer", json=payload, headers={"X-CSRF-Token": token})
    second = player.post("/api/answer", json=payload, headers={"X-CSRF-Token": token})
    assert first.get_json()["duplicate"] is False
    assert second.get_json()["duplicate"] is True
    connection = connect_database(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT score FROM participants").fetchone()["score"] == 1
        assert connection.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"] == 1
    finally:
        connection.close()


def test_concurrent_duplicate_requests_only_score_once(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player, "Папа")
    state = player.get("/api/quiz/state").get_json()
    with player.session_transaction() as session:
        cookie_value = session["participant_id"]
        csrf_token = session["csrf_token"]

    def submit():
        local = app.test_client()
        with local.session_transaction() as session:
            session["participant_id"] = cookie_value
            session["csrf_token"] = csrf_token
        return local.post(
            "/api/answer",
            json={"question_id": state["question"]["id"], "answer": "22:10"},
            headers={"X-CSRF-Token": csrf_token},
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _item: submit(), range(4)))
    assert statuses == [200, 200, 200, 200]
    connection = connect_database(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT score FROM participants").fetchone()["score"] == 1
        assert connection.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"] == 1
    finally:
        connection.close()


def test_multiple_participants_and_tied_ranking(app):
    host = app.test_client()
    start_quiz(host)
    players = [app.test_client() for _ in range(6)]
    for index, player in enumerate(players):
        join(player, f"Гость {index}")
    for question in range(10):
        for player in players:
            assert answer_current(player, "a" if question < 5 else "b").status_code == 200
        assert admin_action(host, "next").status_code == 200
    board = players[0].get("/api/ranking").get_json()["ranking"]
    assert len(board) == 6
    assert {row["place"] for row in board} == {1}
    assert {row["score"] for row in board} == {26}
    assert {row["max_score"] for row in board} == {36}


def test_concurrent_answers_from_multiple_participants_are_not_lost(app):
    host = app.test_client()
    start_quiz(host)
    identities = []
    for index in range(8):
        player = app.test_client()
        join(player, f"Параллельный гость {index}")
        state = player.get("/api/quiz/state").get_json()
        with player.session_transaction() as session:
            identities.append((session["participant_id"], session["csrf_token"], state["question"]["id"]))

    def submit(identity):
        public_id, csrf_token, question_id = identity
        local = app.test_client()
        with local.session_transaction() as session:
            session["participant_id"] = public_id
            session["csrf_token"] = csrf_token
        return local.post(
            "/api/answer", json={"question_id": question_id, "answer": "22:10"},
            headers={"X-CSRF-Token": csrf_token},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(submit, identities)) == [200] * 8
    connection = connect_database(app.config["DATABASE"])
    try:
        assert connection.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"] == 8
        assert connection.execute("SELECT SUM(score) AS total FROM participants").fetchone()["total"] == 64
    finally:
        connection.close()


def test_numeric_answers_receive_points_by_closeness(app):
    host = app.test_client()
    start_quiz(host)
    players = [app.test_client() for _ in range(3)]
    names = ["Точно", "Близко", "Далеко"]
    guesses = ["22:10", "22.00", "21:00"]
    for player, name in zip(players, names):
        join(player, name)
    for player, guess in zip(players, guesses):
        assert answer_current(player, answer=guess).status_code == 200
    assert admin_action(host, "next").status_code == 200
    connection = connect_database(app.config["DATABASE"])
    try:
        scores = {
            row["display_name"]: row["score"]
            for row in connection.execute("SELECT display_name, score FROM participants")
        }
        assert scores == {"Точно": 3, "Близко": 2, "Далеко": 1}
        points = [row["points"] for row in connection.execute("SELECT points FROM answers ORDER BY points DESC")]
        assert points == [3, 2, 1]
    finally:
        connection.close()


def test_choice_question_gives_two_for_correct_and_zero_for_wrong(app):
    host = app.test_client()
    start_quiz(host)
    correct, wrong = app.test_client(), app.test_client()
    join(correct, "Верно")
    join(wrong, "Неверно")
    for _ in range(4):
        assert admin_action(host, "next").status_code == 200
    assert answer_current(correct, option="a").status_code == 200
    assert answer_current(wrong, option="b").status_code == 200
    assert admin_action(host, "next").status_code == 200
    connection = connect_database(app.config["DATABASE"])
    try:
        scores = {
            row["display_name"]: row["score"]
            for row in connection.execute("SELECT display_name, score FROM participants")
        }
        assert scores == {"Верно": 6, "Неверно": 4}
        choice_points = [
            row["points"]
            for row in connection.execute(
                "SELECT points FROM answers WHERE question_index=4 ORDER BY points DESC"
            )
        ]
        assert choice_points == [2, 0]
    finally:
        connection.close()


def test_expired_server_deadline_advances_everyone(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player)
    connection = connect_database(app.config["DATABASE"])
    try:
        connection.execute("UPDATE rounds SET question_deadline=? WHERE status='running'", (time.time() - 1,))
    finally:
        connection.close()
    state = player.get("/api/quiz/state").get_json()
    assert state["question_index"] == 1
    assert state["question"]["id"] == "q2"
    connection = connect_database(app.config["DATABASE"])
    try:
        missed = connection.execute("SELECT option_id FROM answers WHERE question_index=0").fetchone()
        assert missed["option_id"] is None
    finally:
        connection.close()


def test_playful_numeric_validation_and_closed_quiz(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player)
    invalid = answer_current(player, answer="когда рак на горе свистнет")
    assert invalid.status_code == 400
    assert "Часики" in invalid.get_json()["error"]
    question_id = player.get("/api/quiz/state").get_json()["question"]["id"]
    assert admin_action(host, "close").status_code == 200
    with player.session_transaction() as session:
        token = session["csrf_token"]
    closed = player.post(
        "/api/answer",
        json={"question_id": question_id, "answer": "22:10"},
        headers={"X-CSRF-Token": token},
    )
    assert closed.status_code == 409


def test_rejects_unknown_choice_option(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player)
    for _ in range(4):
        assert admin_action(host, "next").status_code == 200
    state = player.get("/api/quiz/state").get_json()
    assert state["question"]["type"] == "choice"
    with player.session_transaction() as session:
        token = session["csrf_token"]
    response = player.post(
        "/api/answer",
        json={"question_id": state["question"]["id"], "option_id": "invented"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 400
    assert "Ваня" in response.get_json()["error"]


def test_public_responses_do_not_leak_correct_answers(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player)
    for response in (player.get("/"), player.get("/api/quiz/state"), player.get("/api/ranking")):
        assert b"correct_option_id" not in response.data
        assert b"correct_value" not in response.data
        assert b"is_correct" not in response.data


def test_manual_timer_and_next_marks_missed(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player)
    assert admin_action(host, "toggle-timer").status_code == 200
    state = player.get("/api/quiz/state").get_json()
    assert state["timer"]["enabled"] is False
    assert admin_action(host, "next").status_code == 200
    connection = connect_database(app.config["DATABASE"])
    try:
        answer = connection.execute("SELECT option_id, is_correct FROM answers").fetchone()
        assert answer["option_id"] is None
        assert answer["is_correct"] == 0
    finally:
        connection.close()


def test_reinitialization_preserves_data(app):
    host, player = app.test_client(), app.test_client()
    start_quiz(host)
    join(player, "Бабушка")
    answer_current(player, "a")
    reloaded = app.__class__
    from app import create_app
    second_app = create_app(dict(app.config))
    connection = connect_database(second_app.config["DATABASE"])
    try:
        assert connection.execute("SELECT COUNT(*) AS n FROM participants").fetchone()["n"] == 1
        assert connection.execute("SELECT score FROM participants").fetchone()["score"] == 1
    finally:
        connection.close()

