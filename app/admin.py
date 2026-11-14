import base64
import csv
import io
import json
import time

import qrcode
from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for

from .configuration import load_quiz_config
from .db import get_db
from .game import (
    advance_question,
    create_round,
    get_current_round,
    ranking,
    round_config,
    sync_round,
    utc_now_iso,
)
from .security import admin_required, check_admin_password, csrf_token, require_csrf


bp = Blueprint("admin", __name__, url_prefix="/admin")


def _qr_data_url(value):
    image = qrcode.make(value)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


@bp.get("")
@bp.get("/")
def admin_page():
    if not session.get("is_admin"):
        return render_template("admin_login.html")
    connection = get_db()
    round_row = sync_round(connection)
    config = round_config(round_row)
    guest_url = url_for("public.home", _external=True)
    return render_template(
        "admin.html", config=config, guest_url=guest_url, qr_data=_qr_data_url(guest_url)
    )


@bp.post("/login")
@require_csrf
def login():
    if not check_admin_password(request.form.get("password", "")):
        return render_template("admin_login.html", login_error="Неверный пароль."), 401
    session.clear()
    session["is_admin"] = True
    csrf_token()
    return redirect(url_for("admin.admin_page"))


@bp.post("/logout")
@admin_required
@require_csrf
def logout():
    session.clear()
    return redirect(url_for("admin.admin_page"))


def _participant_rows(connection, round_id):
    return connection.execute(
        """SELECT p.id, p.display_name, p.score, p.started_at, p.completed_at,
                  COUNT(a.id) AS answered_count,
                  SUM(CASE WHEN a.id IS NOT NULL AND a.option_id IS NULL
                                AND a.numeric_value IS NULL THEN 1 ELSE 0 END) AS missed_count
           FROM participants p
           LEFT JOIN answers a ON a.participant_id=p.id
           WHERE p.round_id=?
           GROUP BY p.id
           ORDER BY p.started_at ASC, p.id ASC""",
        (round_id,),
    ).fetchall()


@bp.get("/api/state")
@admin_required
def state_api():
    connection = get_db()
    round_row = sync_round(connection)
    config = round_config(round_row)
    participants = [dict(row) for row in _participant_rows(connection, round_row["id"])]
    return jsonify(
        round={
            "status": round_row["status"],
            "is_open": bool(round_row["is_open"]),
            "current_question": round_row["current_question"],
            "timer_enabled": bool(round_row["timer_enabled"]),
            "deadline": round_row["question_deadline"],
            "server_time": time.time(),
            "question_seconds": config["question_seconds"],
            "question_total": len(config["questions"]),
            "question_text": (
                config["questions"][round_row["current_question"]]["text"]
                if round_row["status"] != "finished" else "Квиз завершён"
            ),
            "config_hash": round_row["config_hash"][:10],
            "demo_content": bool(config.get("demo_content")),
        },
        participants=participants,
        ranking=ranking(connection, round_row["id"]),
    )


@bp.post("/api/action/<action>")
@admin_required
@require_csrf
def action_api(action):
    payload = request.get_json(silent=True) or {}
    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        round_row = get_current_round(connection)
        config = round_config(round_row)
        now = time.time()
        if action == "open":
            if round_row["status"] == "finished":
                raise ValueError("Завершённый раунд нельзя открыть. Начните новый раунд.")
            connection.execute("UPDATE rounds SET is_open=1 WHERE id=?", (round_row["id"],))
        elif action == "close":
            connection.execute(
                "UPDATE rounds SET is_open=0, timer_enabled=0, question_deadline=NULL WHERE id=?",
                (round_row["id"],),
            )
        elif action == "start":
            if round_row["status"] != "waiting":
                raise ValueError("Раунд уже был запущен.")
            connection.execute(
                """UPDATE rounds SET status='running', is_open=1, current_question=0,
                   timer_enabled=1, question_deadline=?, started_at=? WHERE id=?""",
                (now + config["question_seconds"], utc_now_iso(), round_row["id"]),
            )
        elif action == "next":
            if round_row["status"] != "running":
                raise ValueError("Сейчас нет активного вопроса.")
            advance_question(connection, round_row, now, preserve_schedule=False)
        elif action == "toggle-timer":
            if round_row["status"] != "running":
                raise ValueError("Таймер доступен только во время игры.")
            enabled = not bool(round_row["timer_enabled"])
            connection.execute(
                "UPDATE rounds SET timer_enabled=?, question_deadline=? WHERE id=?",
                (int(enabled), now + config["question_seconds"] if enabled else None, round_row["id"]),
            )
        elif action == "reset":
            if payload.get("confirmation") != "ОЧИСТИТЬ":
                raise ValueError("Для нового раунда введите ОЧИСТИТЬ.")
            # Validate the latest on-disk configuration before archiving anything.
            load_quiz_config(__import__("flask").current_app.config["QUIZ_CONFIG"])
            connection.execute(
                "UPDATE rounds SET status='archived', is_open=0, timer_enabled=0, question_deadline=NULL WHERE id=?",
                (round_row["id"],),
            )
            create_round(connection)
            session.pop("participant_id", None)
        else:
            connection.rollback()
            return jsonify(error="Неизвестное действие."), 404
        connection.commit()
        return jsonify(ok=True)
    except ValueError as exc:
        connection.rollback()
        return jsonify(error=str(exc)), 409
    except Exception:
        connection.rollback()
        raise


def _excel_safe(value):
    value = str(value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


@bp.get("/results.csv")
@admin_required
def results_csv():
    connection = get_db()
    round_row = sync_round(connection)
    rows = _participant_rows(connection, round_row["id"])
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Имя", "Баллы", "Ответов", "Пропущено", "Начало", "Завершение"])
    for row in rows:
        writer.writerow(
            [
                _excel_safe(row["display_name"]), row["score"], row["answered_count"],
                row["missed_count"] or 0, row["started_at"], row["completed_at"] or "",
            ]
        )
    content = "\ufeff" + stream.getvalue()
    return Response(
        content,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=vania-quiz-results.csv"},
    )

