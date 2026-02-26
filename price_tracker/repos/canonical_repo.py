from __future__ import annotations

import sqlite3


class CanonicalItemRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, key: str, label: str, size: float, unit: str) -> int:
        key = key.strip()
        label = label.strip()
        unit = unit.strip()

        if not key:
            raise ValueError("canonical key cannot be empty")
        if not label:
            raise ValueError("canonical label cannot be empty")
        if float(size) <= 0:
            raise ValueError("canonical size must be > 0")
        if not unit:
            raise ValueError("canonical unit cannot be empty")

        self.conn.execute(
            """
            INSERT INTO canonical_item(key, label, size, unit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              label=excluded.label,
              size=excluded.size,
              unit=excluded.unit
            """,
            (key, label, float(size), unit),
        )
        row = self.conn.execute("SELECT id FROM canonical_item WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise RuntimeError("failed to fetch canonical_item after upsert")
        return int(row["id"])

    def get_id_by_key(self, key: str) -> int | None:
        key = key.strip()
        if not key:
            return None
        row = self.conn.execute("SELECT id FROM canonical_item WHERE key = ?", (key,)).fetchone()
        return int(row["id"]) if row else None

    def get_by_key(self, key: str) -> dict | None:
        key = key.strip()
        if not key:
            return None
        row = self.conn.execute(
            "SELECT id, key, label, size, unit FROM canonical_item WHERE key = ?",
            (key,),
        ).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, key, label, size, unit FROM canonical_item ORDER BY key"
        ).fetchall()
        return [dict(r) for r in rows]