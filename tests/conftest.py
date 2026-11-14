import json

import pytest

from app import create_app


@pytest.fixture()
def config_file(tmp_path):
    questions = []
    for index in range(10):
        question = {
            "id": f"q{index + 1}", "text": f"Тестовый вопрос {index + 1}",
            "caption": "", "image": "",
        }
        if index < 4:
            kinds = ["time", "months", "weight_grams", "height_cm"]
            values = [1330, 8, 3150, 51]
            question.update(
                type="numeric", answer_kind=kinds[index], correct_value=values[index],
                placeholder="Введите значение",
            )
        else:
            question.update(
                type="choice",
                options=[
                    {"id": "a", "text": "Верно"},
                    {"id": "b", "text": "Неверно"},
                    {"id": "c", "text": "Тоже неверно"},
                ],
                correct_option_id="a",
            )
        questions.append(question)
    data = {
        "child_name": "Ваня", "title": "Тестовый квиз", "subtitle": "Первый год",
        "welcome": "Добро пожаловать", "event_date": "", "final_title": "Финал",
        "final_text": "Спасибо", "demo_content": False, "question_seconds": 60,
        "questions": questions,
    }
    path = tmp_path / "quiz.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture()
def app(tmp_path, config_file):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "quiz.sqlite"),
            "QUIZ_CONFIG": str(config_file),
            "SECRET_KEY": "test-secret",
            "ADMIN_PASSWORD": "host-secret",
            "SESSION_COOKIE_SECURE": False,
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client, path="/"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]


def admin_login(client):
    token = csrf(client, "/admin")
    response = client.post("/admin/login", data={"csrf_token": token, "password": "host-secret"})
    assert response.status_code == 302
    with client.session_transaction() as session:
        return session["csrf_token"]


def admin_action(client, action, payload=None):
    with client.session_transaction() as session:
        token = session["csrf_token"]
    return client.post(
        f"/admin/api/action/{action}", json=payload or {}, headers={"X-CSRF-Token": token}
    )


def join(client, name="Гость"):
    token = csrf(client)
    response = client.post("/join", data={"csrf_token": token, "name": name})
    assert response.status_code == 302


def answer_current(client, option="a", answer=None):
    state = client.get("/api/quiz/state").get_json()
    with client.session_transaction() as session:
        token = session["csrf_token"]
    question = state["question"]
    if question["type"] == "numeric":
        defaults = {"q1": "22:10", "q2": "8 месяцев", "q3": "3,15 кг", "q4": "51 см"}
        payload = {"question_id": question["id"], "answer": answer or defaults[question["id"]]}
    else:
        payload = {"question_id": question["id"], "option_id": option}
    return client.post(
        "/api/answer",
        json=payload,
        headers={"X-CSRF-Token": token},
    )

