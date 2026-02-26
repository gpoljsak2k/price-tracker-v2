from __future__ import annotations

import sqlite3


class StoreRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get_or_create(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("store name cannot be empty")

        self.conn.execute("INSERT OR IGNORE INTO store(name) VALUES (?)", (name,))
        row = self.conn.execute("SELECT id FROM store WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise RuntimeError("failed to fetch store after insert")
        return int(row["id"])

    def get_id_by_name(self, name: str) -> int | None:
        name = name.strip()
        if not name:
            return None
        row = self.conn.execute("SELECT id FROM store WHERE name = ?", (name,)).fetchone()
        return int(row["id"]) if row else None

    def list(self) -> list[tuple[int, str]]:
        rows = self.conn.execute("SELECT id, name FROM store ORDER BY name").fetchall()
        return [(int(r["id"]), str(r["name"])) for r in rows]