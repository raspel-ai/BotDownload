import sqlite3
from bot.database import initialize


def test_database_initialized_idempotently(tmp_path):
    path = tmp_path / "db.sqlite"
    initialize(path)
    initialize(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_meta").fetchall() == [(1,)]
