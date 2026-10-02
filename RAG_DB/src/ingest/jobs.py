from datetime import datetime
from core.db import get_connection

def create_job(job_id: str, source_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, source_id, stage, progress, units_created, error, updated_at) "
            "VALUES (?, ?, 'initialized', 0.0, 0, NULL, ?)",
            (job_id, source_id, datetime.utcnow().isoformat() + "Z")
        )
        conn.commit()
    finally:
        conn.close()

def update_job_progress(job_id: str, stage: str, progress: float = 0.0, units_created: int = 0) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE jobs SET stage=?, progress=?, units_created=units_created+?, updated_at=? WHERE job_id=?",
            (stage, progress, units_created, datetime.utcnow().isoformat() + "Z", job_id)
        )
        conn.commit()
    finally:
        conn.close()

def fail_job(job_id: str, error: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE jobs SET stage='failed', error=?, updated_at=? WHERE job_id=?",
            (str(error), datetime.utcnow().isoformat() + "Z", job_id)
        )
        conn.execute(
            "UPDATE sources SET status='failed' WHERE source_id=(SELECT source_id FROM jobs WHERE job_id=?)",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()
