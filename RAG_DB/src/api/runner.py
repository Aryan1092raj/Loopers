"""Background pipeline jobs: run `pipeline.py <stage>` subprocesses and report progress in `jobs`.

One job at a time: the stages share `raw/`, the registry and the embedded Qdrant folder.
Output goes to `metadata/jobs/<job_id>.log` for the UI to tail.
"""
import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from dotenv import dotenv_values

from core.db import get_connection
from ingest.jobs import create_job, fail_job, update_job_progress

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "metadata" / "jobs"

STAGE_ARGS = {
    "ingest": ["ingest"],
    "extract": ["extract"],
    "label": ["label"],
    "embed": ["embed"],
    "concepts": ["concepts"],
    "eval": ["eval", "--k", "5"],
}
# Job stage names reported to the UI (TRD §6.1 document stages; `embed` also writes the units)
STAGE_LABEL = {"ingest": "parse", "extract": "parse", "label": "figures", "media": "transcribe", "embed": "embed", "concepts": "concepts", "eval": "eval"}
DOCUMENT_STAGES = ["ingest", "extract", "label", "embed", "concepts"]
MEDIA_STAGES = ["media", "concepts"]  # video and audio sources: transcribe + embed clips, then tag concepts

_busy = threading.Lock()
# A stuck stage (e.g. endless 429 retries) must not hold `_busy` forever: it is killed on timeout or via cancel().
STAGE_TIMEOUT = int(os.environ.get("JOB_STAGE_TIMEOUT", "1800"))
_proc: subprocess.Popen | None = None
_proc_job: str | None = None
_cancelled: str | None = None


class Busy(Exception):
    pass


def log_path(job_id: str) -> Path:
    return LOG_DIR / f"{job_id}.log"


def start(stages: list[str], source_id: str | None = None) -> str:
    """Start stages in a background thread and return the job id. Raises Busy if a job is running."""
    if not _busy.acquire(blocking=False):
        raise Busy()
    job_id = f"j_{uuid.uuid4().hex[:12]}"
    try:
        create_job(job_id, source_id)
    except Exception:
        _busy.release()
        raise
    threading.Thread(target=_run, args=(job_id, stages, source_id), daemon=True).start()
    return job_id


def _run(job_id: str, stages: list[str], source_id: str | None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # The CLI child may re-exec to set GPU library paths; only the API process must not.
    env = {k: v for k, v in os.environ.items() if k != "LOOPERS_NO_REEXEC"}
    # Re-read .env per job: the API process loaded it once at import, so key edits would otherwise need a restart.
    env.update({k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None})
    env["PYTHONUNBUFFERED"] = "1"
    try:
        with open(log_path(job_id), "a", buffering=1) as log:
            for i, stage in enumerate(stages):
                update_job_progress(job_id, STAGE_LABEL[stage], i / len(stages))
                log.write(f"\n=== {stage} ===\n")
                args = ["media", source_id, "--job-id", job_id] if stage == "media" else STAGE_ARGS[stage]
                code = _run_stage([sys.executable, str(ROOT / "src" / "pipeline.py"), *args], env, log, job_id)
                if _cancelled == job_id:
                    fail_job(job_id, "cancelled")
                    return
                if code != 0:
                    fail_job(job_id, f"{stage} exited with code {code}")
                    return
            update_job_progress(job_id, "done", 1.0, units_created=_count_units(source_id) if source_id else 0)
            if source_id:
                _set_source_status(source_id, "done")
    except Exception as e:
        fail_job(job_id, str(e))
    finally:
        _busy.release()


def _run_stage(cmd: list[str], env: dict, log, job_id: str) -> int:
    """Run one stage in its own process group so timeout and cancel also reach its children (ffmpeg, workers)."""
    global _proc, _proc_job
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    _proc, _proc_job = proc, job_id
    try:
        return proc.wait(timeout=STAGE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill(proc)
        raise
    finally:
        _proc = _proc_job = None


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def cancel(job_id: str) -> bool:
    """Kill the running stage of `job_id`; the job is then marked failed("cancelled"). False if it isn't the running job."""
    global _cancelled
    proc = _proc
    if proc is None or _proc_job != job_id:
        return False
    _cancelled = job_id
    _kill(proc)
    return True


def _count_units(source_id: str) -> int:
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM units WHERE source_id = ?", (source_id,)).fetchone()[0]
    finally:
        conn.close()


def _set_source_status(source_id: str, status: str) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE sources SET status = ? WHERE source_id = ?", (status, source_id))
        conn.commit()
    finally:
        conn.close()
