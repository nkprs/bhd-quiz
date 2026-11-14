import sqlite3

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK (status IN ('waiting', 'running', 'finished', 'archived')),
    is_open INTEGER NOT NULL DEFAULT 0 CHECK (is_open IN (0, 1)),
    current_question INTEGER NOT NULL DEFAULT 0,
    timer_enabled INTEGER NOT NULL DEFAULT 0 CHECK (timer_enabled IN (0, 1)),
    question_deadline REAL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 40),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    score INTEGER NOT NULL DEFAULT 0 CHECK (score >= 0),
    UNIQUE(round_id, public_id)
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    question_index INTEGER NOT NULL,
    option_id TEXT,
    answer_text TEXT,
    numeric_value REAL,
    is_correct INTEGER NOT NULL DEFAULT 0 CHECK (is_correct IN (0, 1)),
    points INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
    answered_at TEXT NOT NULL,
    UNIQUE(participant_id, question_index)
);

CREATE INDEX IF NOT EXISTS idx_participants_round ON participants(round_id);
CREATE INDEX IF NOT EXISTS idx_answers_participant ON answers(participant_id);
"""


def connect_database(path):
    connection = sqlite3.connect(path, timeout=8.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 8000")
    # DELETE is conservative for PythonAnywhere's persistent filesystem.
    connection.execute("PRAGMA journal_mode = DELETE")
    return connection


def get_db():
    if "db" not in g:
        g.db = connect_database(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_schema(connection):
    connection.executescript(SCHEMA)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(answers)")}
    migrations = {
        "answer_text": "ALTER TABLE answers ADD COLUMN answer_text TEXT",
        "numeric_value": "ALTER TABLE answers ADD COLUMN numeric_value REAL",
        "points": "ALTER TABLE answers ADD COLUMN points INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0)",
    }
    for name, statement in migrations.items():
        if name not in columns:
            connection.execute(statement)


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command():
        from .game import ensure_current_round

        connection = get_db()
        init_schema(connection)
        ensure_current_round(connection)
        print("База квиза готова.")

