import sqlite3

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from .db import get_db
from .game import (
    create_participant,
    find_participant,
    get_current_round,
    public_state,
    ranking,
    round_config,
    sync_round,
    utc_now_iso,
)
from .security import csrf_token, require_csrf
from .scoring import PlayfulAnswerError, parse_numeric_answer, recompute_scores


bp = Blueprint("public", __name__)


def _context():
    connection = get_db()
    round_row = sync_round(connection)
    participant = find_participant(connection, session.get("participant_id"), round_row["id"])
    return connection, round_row, participant, round_config(round_row)


@bp.get("/")
def home():
    _connection, round_row, participant, config = _context()
    if participant and round_row["status"] in ("running", "waiting"):
        return render_template("quiz.html", config=config, participant=participant)
    if participant and round_row["status"] == "finished":
        return redirect(url_for("public.result_page"))
    return render_template("home.html", config=config, round=round_row, participant=participant)


@bp.post("/join")
@require_csrf
def join():
    name = " ".join(request.form.get("name", "").strip().split())
    if not 1 <= len(name) <= 40:
        _connection, round_row, _participant, config = _context()
        return render_template(
            "home.html", config=config, round=round_row, participant=None,
            join_error="Введите имя длиной от 1 до 40 символов."
        ), 400
    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        round_row = get_current_round(connection)
        if not round_row["is_open"] or round_row["status"] == "finished":
            connection.rollback()
            return redirect(url_for("public.home"))
        existing = find_participant(connection, session.get("participant_id"), round_row["id"])
        if existing is None:
            session["participant_id"] = create_participant(connection, round_row, name)
            recompute_scores(connection, round_row, round_config(round_row))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return redirect(url_for("public.home"))


@bp.get("/api/quiz/state")
def state_api():
    connection, round_row, participant, _config = _context()
    state = public_state(connection, round_row, participant)
    state["server_time"] = __import__("time").time()
    return jsonify(state)


@bp.post("/api/answer")
@require_csrf
def answer_api():
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    option_id = payload.get("option_id")
    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        round_row = get_current_round(connection)
        participant = find_participant(connection, session.get("participant_id"), round_row["id"])
        if participant is None:
            connection.rollback()
            return jsonify(error="Сессия участника не найдена. Вернитесь на старт."), 401

        config = round_config(round_row)
        question_index = next(
            (i for i, item in enumerate(config["questions"]) if item["id"] == question_id), None
        )
        if question_index is None:
            connection.rollback()
            return jsonify(error="Неизвестный вопрос."), 400

        existing = connection.execute(
            "SELECT * FROM answers WHERE participant_id=? AND question_index=?",
            (participant["id"], question_index),
        ).fetchone()
        if existing is not None:
            connection.commit()
            if existing["option_id"] is None and existing["numeric_value"] is None:
                return jsonify(error="Время на этот вопрос уже закончилось."), 409
            return jsonify(ok=True, duplicate=True)

        now_epoch = __import__("time").time()
        if (
            round_row["status"] != "running"
            or not round_row["is_open"]
            or question_index != round_row["current_question"]
            or (
                round_row["timer_enabled"]
                and round_row["question_deadline"] is not None
                and round_row["question_deadline"] <= now_epoch
            )
        ):
            connection.rollback()
            sync_round(connection, now_epoch)
            return jsonify(error="Этот вопрос уже закрыт ведущим или таймером."), 409

        question = config["questions"][question_index]
        if question.get("type", "choice") == "numeric":
            answer_text = str(payload.get("answer", "")).strip()[:80]
            try:
                numeric_value = parse_numeric_answer(question, answer_text)
            except PlayfulAnswerError as exc:
                connection.rollback()
                return jsonify(error=str(exc)), 400
            connection.execute(
                """INSERT INTO answers
                   (round_id, participant_id, question_id, question_index, answer_text,
                    numeric_value, is_correct, answered_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    round_row["id"], participant["id"], question_id, question_index,
                    answer_text, numeric_value, utc_now_iso(),
                ),
            )
        else:
            valid_ids = {item["id"] for item in question["options"]}
            if option_id not in valid_ids:
                connection.rollback()
                return jsonify(error="Выберите один из вариантов — Ваня разрешает только эти 🙂"), 400
            is_correct = int(option_id == question["correct_option_id"])
            connection.execute(
                """INSERT INTO answers
                   (round_id, participant_id, question_id, question_index, option_id, is_correct, answered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    round_row["id"], participant["id"], question_id, question_index,
                    option_id, is_correct, utc_now_iso(),
                ),
            )
        recompute_scores(connection, round_row, config)
        connection.commit()
        return jsonify(ok=True, duplicate=False)
    except sqlite3.IntegrityError:
        connection.rollback()
        return jsonify(ok=True, duplicate=True)
    except Exception:
        connection.rollback()
        raise


@bp.get("/result")
def result_page():
    connection, round_row, participant, config = _context()
    if participant is None:
        return redirect(url_for("public.home"))
    if round_row["status"] != "finished":
        return redirect(url_for("public.home"))
    board = ranking(connection, round_row["id"], participant["id"])
    own_place = next((row["place"] for row in board if row["is_own"]), None)
    max_score = board[0]["max_score"] if board else 0
    return render_template(
        "result.html", config=config, participant=participant, ranking=board,
        own_place=own_place, max_score=max_score,
        wish_error=session.pop("wish_error", None),
    )


@bp.post("/wish")
@require_csrf
def submit_wish():
    raw_wish = str(request.form.get("wish", "")).replace("\r\n", "\n").replace("\r", "\n")
    wish = "\n".join(line.rstrip() for line in raw_wish.strip().splitlines())
    if not wish:
        session["wish_error"] = "Даже Ваня пока пишет короче, но одно пожелание всё-таки нужно 🙂"
        return redirect(url_for("public.result_page", _anchor="wish"))
    if len(wish) > 500:
        session["wish_error"] = "Получилась почти семейная сага. Уложимся в 500 символов 📚"
        return redirect(url_for("public.result_page", _anchor="wish"))

    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        round_row = get_current_round(connection)
        participant = find_participant(connection, session.get("participant_id"), round_row["id"])
        if participant is None:
            connection.rollback()
            return redirect(url_for("public.home"))
        if round_row["status"] != "finished":
            connection.rollback()
            session["wish_error"] = "Пожелания откроются после последнего вопроса — всему своё время 🎈"
            return redirect(url_for("public.home"))
        if participant["wish_text"]:
            connection.commit()
            if participant["wish_text"] != wish:
                session["wish_error"] = "Пожелание уже запечатано в семейный архив ✉️"
            return redirect(url_for("public.result_page", _anchor="wish"))
        connection.execute(
            "UPDATE participants SET wish_text=?, wish_submitted_at=? WHERE id=?",
            (wish, utc_now_iso(), participant["id"]),
        )
        connection.commit()
        return redirect(url_for("public.result_page", _anchor="wish"))
    except Exception:
        connection.rollback()
        raise


@bp.get("/ranking")
def ranking_page():
    connection, round_row, participant, config = _context()
    board = ranking(connection, round_row["id"], participant["id"] if participant else None)
    return render_template("ranking.html", config=config, ranking=board)


@bp.get("/api/ranking")
def ranking_api():
    connection, round_row, participant, _config = _context()
    return jsonify(
        ranking=ranking(connection, round_row["id"], participant["id"] if participant else None),
        round_status=round_row["status"],
    )

