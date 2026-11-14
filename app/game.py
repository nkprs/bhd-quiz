import json
import secrets
import time
from datetime import datetime, timezone

from flask import current_app

from .configuration import load_quiz_config, public_question
from .scoring import maximum_score, recompute_scores


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def round_config(round_row):
    return json.loads(round_row["config_json"])


def create_round(connection):
    config, config_hash = load_quiz_config(current_app.config["QUIZ_CONFIG"])
    now = utc_now_iso()
    cursor = connection.execute(
        """INSERT INTO rounds
           (status, is_open, current_question, timer_enabled, question_deadline,
            created_at, config_json, config_hash)
           VALUES ('waiting', 0, 0, 0, NULL, ?, ?, ?)""",
        (now, json.dumps(config, ensure_ascii=False), config_hash),
    )
    round_id = cursor.lastrowid
    connection.execute(
        "INSERT INTO app_state(key, value) VALUES ('current_round_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(round_id),),
    )
    return round_id


def ensure_current_round(connection):
    row = connection.execute(
        """SELECT r.* FROM rounds r
           JOIN app_state s ON s.key='current_round_id' AND CAST(s.value AS INTEGER)=r.id"""
    ).fetchone()
    if row is not None:
        return row
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """SELECT r.* FROM rounds r
               JOIN app_state s ON s.key='current_round_id' AND CAST(s.value AS INTEGER)=r.id"""
        ).fetchone()
        if row is None:
            round_id = create_round(connection)
            row = connection.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone()
        connection.commit()
        return row
    except Exception:
        connection.rollback()
        raise


def get_current_round(connection):
    return connection.execute(
        """SELECT r.* FROM rounds r
           JOIN app_state s ON s.key='current_round_id' AND CAST(s.value AS INTEGER)=r.id"""
    ).fetchone()


def _fill_missed_for_question(connection, round_row, question_index, now_iso):
    config = round_config(round_row)
    question = config["questions"][question_index]
    connection.execute(
        """INSERT OR IGNORE INTO answers
           (round_id, participant_id, question_id, question_index, option_id, is_correct, answered_at)
           SELECT ?, p.id, ?, ?, NULL, 0, ?
           FROM participants p WHERE p.round_id=?""",
        (round_row["id"], question["id"], question_index, now_iso, round_row["id"]),
    )


def _finish_round(connection, round_row, now_iso):
    connection.execute(
        """UPDATE participants
           SET completed_at=COALESCE(completed_at, ?)
           WHERE round_id=?""",
        (now_iso, round_row["id"]),
    )
    connection.execute(
        """UPDATE rounds SET status='finished', is_open=0, timer_enabled=0,
           question_deadline=NULL, finished_at=? WHERE id=?""",
        (now_iso, round_row["id"]),
    )


def advance_question(connection, round_row, now_epoch=None, preserve_schedule=False):
    now_epoch = now_epoch if now_epoch is not None else time.time()
    now_iso = utc_now_iso()
    config = round_config(round_row)
    index = round_row["current_question"]
    _fill_missed_for_question(connection, round_row, index, now_iso)
    if index >= len(config["questions"]) - 1:
        _finish_round(connection, round_row, now_iso)
        recompute_scores(connection, round_row, config)
        return

    next_index = index + 1
    deadline = None
    if round_row["timer_enabled"]:
        if preserve_schedule and round_row["question_deadline"] is not None:
            deadline = round_row["question_deadline"] + config["question_seconds"]
        else:
            deadline = now_epoch + config["question_seconds"]
    connection.execute(
        "UPDATE rounds SET current_question=?, question_deadline=? WHERE id=?",
        (next_index, deadline, round_row["id"]),
    )
    recompute_scores(connection, round_row, config)


def sync_round(connection, now_epoch=None):
    now_epoch = now_epoch if now_epoch is not None else time.time()
    connection.execute("BEGIN IMMEDIATE")
    try:
        round_row = get_current_round(connection)
        while (
            round_row is not None
            and round_row["status"] == "running"
            and round_row["is_open"]
            and round_row["timer_enabled"]
            and round_row["question_deadline"] is not None
            and round_row["question_deadline"] <= now_epoch
        ):
            advance_question(connection, round_row, now_epoch, preserve_schedule=True)
            round_row = get_current_round(connection)
        connection.commit()
        return round_row
    except Exception:
        connection.rollback()
        raise


def find_participant(connection, public_id, round_id):
    if not public_id:
        return None
    return connection.execute(
        "SELECT * FROM participants WHERE public_id=? AND round_id=?",
        (public_id, round_id),
    ).fetchone()


def create_participant(connection, round_row, display_name):
    now = utc_now_iso()
    public_id = secrets.token_urlsafe(32)
    cursor = connection.execute(
        "INSERT INTO participants(public_id, round_id, display_name, started_at) VALUES (?, ?, ?, ?)",
        (public_id, round_row["id"], display_name, now),
    )
    participant_id = cursor.lastrowid
    config = round_config(round_row)
    if round_row["status"] == "running" and round_row["current_question"] > 0:
        for index in range(round_row["current_question"]):
            connection.execute(
                """INSERT INTO answers
                   (round_id, participant_id, question_id, question_index, option_id, is_correct, answered_at)
                   VALUES (?, ?, ?, ?, NULL, 0, ?)""",
                (round_row["id"], participant_id, config["questions"][index]["id"], index, now),
            )
    return public_id


def ranking(connection, round_id, own_participant_id=None):
    rows = connection.execute(
        """SELECT id, display_name, score, completed_at
           FROM participants
           WHERE round_id=? AND completed_at IS NOT NULL
           ORDER BY score DESC, completed_at ASC, id ASC""",
        (round_id,),
    ).fetchall()
    round_row = connection.execute("SELECT config_json FROM rounds WHERE id=?", (round_id,)).fetchone()
    participant_count = connection.execute(
        "SELECT COUNT(*) AS n FROM participants WHERE round_id=?", (round_id,)
    ).fetchone()["n"]
    config = json.loads(round_row["config_json"])
    max_points = maximum_score(config, participant_count)
    result = []
    previous_score = None
    place = 0
    for index, row in enumerate(rows, 1):
        if row["score"] != previous_score:
            place = index
            previous_score = row["score"]
        result.append(
            {
                "place": place,
                "name": row["display_name"],
                "score": row["score"],
                "max_score": max_points,
                "is_own": row["id"] == own_participant_id,
            }
        )
    return result


def public_state(connection, round_row, participant):
    config = round_config(round_row)
    response = {
        "round_status": round_row["status"],
        "quiz_open": bool(round_row["is_open"]),
        "title": config["title"],
        "subtitle": config["subtitle"],
        "child_name": config["child_name"],
        "demo_content": bool(config.get("demo_content")),
        "question_total": len(config["questions"]),
        "question_index": round_row["current_question"],
        "timer": {
            "enabled": bool(round_row["timer_enabled"]),
            "deadline": round_row["question_deadline"],
            "duration": config["question_seconds"],
        },
        "participant": None,
        "question": None,
        "already_answered": False,
    }
    if participant is not None:
        response["participant"] = {"name": participant["display_name"], "score": participant["score"]}
    if round_row["status"] == "running" and participant is not None:
        index = round_row["current_question"]
        answer = connection.execute(
            "SELECT id FROM answers WHERE participant_id=? AND question_index=?",
            (participant["id"], index),
        ).fetchone()
        response["already_answered"] = answer is not None
        if answer is None and round_row["is_open"]:
            response["question"] = public_question(config["questions"][index])
    if round_row["status"] == "finished" and participant is not None:
        participant_count = connection.execute(
            "SELECT COUNT(*) AS n FROM participants WHERE round_id=?", (round_row["id"],)
        ).fetchone()["n"]
        response["result"] = {
            "score": participant["score"],
            "total": maximum_score(config, participant_count),
        }
    return response

