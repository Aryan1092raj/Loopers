"""Copy the worker's knowledge tables into this process's SQLite after an ingest job finishes."""
import worker_client
from api.worker import KNOWLEDGE_TABLES
from core.db import get_connection


def sync_knowledge() -> None:
    data = worker_client.call("GET", "/worker/export", timeout=300).json()
    if not data["units"]["rows"]:  # a worker with a fresh database must not wipe the course
        raise ValueError("worker export has no units; seed the worker's course.db first")
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")  # learner tables point at concepts that are replaced
        for table in reversed(KNOWLEDGE_TABLES):
            conn.execute(f"DELETE FROM {table}")
        for table in KNOWLEDGE_TABLES:
            cols, rows = data[table]["columns"], data[table]["rows"]
            if rows:
                conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
        conn.commit()
    finally:
        conn.close()
