"""Chat sessions in SQLite (`chat_sessions`, `chat_turns`). Replaces the module-global history in chat.py."""
import json
from datetime import datetime, timezone

from core.db import get_connection


def add_turn(session_id: str, student_id: str | None, role: str, content: str,
             mode: str | None = None, citations: list | None = None, gate: dict | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO chat_sessions (session_id, student_id, created_at) VALUES (?, ?, ?)",
            (session_id, student_id, now),
        )
        conn.execute(
            "INSERT INTO chat_turns (session_id, role, content, mode, citations_json, gate_json, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, mode, json.dumps(citations) if citations is not None else None,
             json.dumps(gate) if gate is not None else None, now),
        )
        conn.commit()
    finally:
        conn.close()


def history(session_id: str, limit: int = 6) -> list[dict]:
    """The last `limit` turns of one session, oldest first, as {"role", "content"}."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM chat_turns WHERE session_id = ? ORDER BY turn_id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [{"role": role, "content": content} for role, content in reversed(rows)]
