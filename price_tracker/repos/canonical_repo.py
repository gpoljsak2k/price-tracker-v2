from __future__ import annotations

import sqlite3


class CanonicalItemRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, family_key: str, label: str, size: float, unit: str) -> int:
        family_key = family_key.strip()
        label = label.strip()
        unit = unit.strip()

        if not family_key:
            raise ValueError("family_key cannot be empty")
        if not label:
            raise ValueError("canonical label cannot be empty")
        if float(size) <= 0:
            raise ValueError("canonical size must be > 0")
        if not unit:
            raise ValueError("canonical unit cannot be empty")

        # Manual upsert (portable across SQLite versions)
        row = self.conn.execute(
            """
            SELECT id FROM canonical_item
            WHERE family_key=? AND size=? AND unit=?
            """,
            (family_key, float(size), unit),
        ).fetchone()

        if row:
            cid = int(row["id"])
            self.conn.execute(
                """
                UPDATE canonical_item
                SET label=?, size=?, unit=?
                WHERE id=?
                """,
                (label, float(size), unit, cid),
            )
            return cid

        self.conn.execute(
            """
            INSERT INTO canonical_item(family_key, label, size, unit)
            VALUES (?, ?, ?, ?)
            """,
            (family_key, label, float(size), unit),
        )

        row2 = self.conn.execute(
            """
            SELECT id FROM canonical_item
            WHERE family_key=? AND size=? AND unit=?
            """,
            (family_key, float(size), unit),
        ).fetchone()
        if row2 is None:
            raise RuntimeError("failed to fetch canonical_item after insert")
        return int(row2["id"])

    def get_id_by_family_size_unit(self, family_key: str, size: float, unit: str) -> int | None:
        family_key = family_key.strip()
        unit = unit.strip()
        if not family_key or not unit or float(size) <= 0:
            return None

        row = self.conn.execute(
            """
            SELECT id FROM canonical_item
            WHERE family_key=? AND size=? AND unit=?
            """,
            (family_key, float(size), unit),
        ).fetchone()
        return int(row["id"]) if row else None

    def get_by_family_size_unit(self, family_key: str, size: float, unit: str) -> dict | None:
        family_key = family_key.strip()
        unit = unit.strip()
        if not family_key or not unit or float(size) <= 0:
            return None

        row = self.conn.execute(
            """
            SELECT id, family_key, label, size, unit
            FROM canonical_item
            WHERE family_key=? AND size=? AND unit=?
            """,
            (family_key, float(size), unit),
        ).fetchone()
        return dict(row) if row else None

    def get_many_by_family_key(self, family_key: str) -> list[dict]:
        family_key = family_key.strip()
        if not family_key:
            return []
        rows = self.conn.execute(
            """
            SELECT id, family_key, label, size, unit
            FROM canonical_item
            WHERE family_key=?
            ORDER BY unit, size
            """,
            (family_key,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, family_key, label, size, unit
            FROM canonical_item
            ORDER BY family_key, unit, size
            """
        ).fetchall()
        return [dict(r) for r in rows]