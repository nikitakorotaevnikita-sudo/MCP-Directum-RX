import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class MetricsStorage:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    duration_ms INTEGER,
                    payload TEXT NOT NULL
                )
                """
            )

    def record_chat_request(self, scenario: str, duration_ms: int, success: bool = True) -> None:
        self._insert("chat", scenario, success, duration_ms, {"scenario": scenario})

    def record_tool_call(
        self,
        name: str,
        success: bool,
        duration_ms: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._insert("tool_call", name, success, duration_ms, payload or {})

    def record_action_item_create(self, mode: str) -> None:
        self._insert("action_item_create", mode, True, None, {"mode": mode})

    def record_feedback(self, rating: str) -> None:
        self._insert("feedback", rating, True, None, {"rating": rating})

    def summary(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT type, name, success, duration_ms, payload, ts FROM events ORDER BY id DESC"
            ).fetchall()

        chat_rows = [row for row in rows if row["type"] == "chat"]
        scenario_counts: dict[str, int] = {}
        for row in chat_rows:
            scenario_counts[row["name"]] = scenario_counts.get(row["name"], 0) + 1

        return {
            "chat_requests": len(chat_rows),
            "scenario_counts": scenario_counts,
            "action_item_previews": sum(
                1 for row in rows if row["type"] == "action_item_create" and row["name"] == "preview"
            ),
            "action_item_confirmed": sum(
                1 for row in rows if row["type"] == "action_item_create" and row["name"] == "confirmed"
            ),
            "errors": sum(1 for row in rows if not row["success"]),
            "latest_tool_calls": [
                {
                    "name": row["name"],
                    "success": bool(row["success"]),
                    "duration_ms": row["duration_ms"],
                    "ts": row["ts"],
                }
                for row in rows
                if row["type"] == "tool_call"
            ][:10],
            "feedback": [row["name"] for row in rows if row["type"] == "feedback"],
        }

    def _insert(
        self,
        type_: str,
        name: str,
        success: bool,
        duration_ms: int | None,
        payload: dict[str, Any],
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO events (ts, type, name, success, duration_ms, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), type_, name, int(success), duration_ms, json.dumps(payload, ensure_ascii=False)),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
