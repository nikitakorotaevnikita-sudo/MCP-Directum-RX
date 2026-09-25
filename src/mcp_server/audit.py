import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

USER_HASH_LENGTH = 16


class ToolUsageStore:
    """Метрики использования тулов для анализа гипотез: какой тул, успех, длительность. Без кредов."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tool_calls ("
                "ts REAL NOT NULL, tool TEXT NOT NULL, ok INTEGER NOT NULL, "
                "error_kind TEXT, duration_ms INTEGER NOT NULL, user_hash TEXT)"
            )

    def record(self, tool: str, ok: bool, error_kind: str | None, duration_ms: int, user_hash: str | None) -> None:
        short_hash = user_hash[:USER_HASH_LENGTH] if user_hash else None
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tool_calls (ts, tool, ok, error_kind, duration_ms, user_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), tool, int(ok), error_kind, duration_ms, short_hash),
            )

    def summary(self) -> list[dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT tool, COUNT(*), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), AVG(duration_ms) "
                "FROM tool_calls GROUP BY tool ORDER BY COUNT(*) DESC"
            ).fetchall()
        return [
            {"tool": tool, "calls": calls, "errors": errors, "avg_duration_ms": round(avg or 0)}
            for tool, calls, errors, avg in rows
        ]

    def user_hashes(self) -> list[str]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_hash FROM tool_calls WHERE user_hash IS NOT NULL ORDER BY user_hash"
            ).fetchall()
        return [row[0] for row in rows]
