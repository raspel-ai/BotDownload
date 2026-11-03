"""SQLite bootstrap; operational tables are introduced in later stages."""
import sqlite3
from pathlib import Path


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
        db.execute("INSERT INTO schema_meta(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta)")
        result = db.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError("SQLite: проверка целостности не пройдена")
