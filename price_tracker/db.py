from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str = "schema.sql") -> None:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise