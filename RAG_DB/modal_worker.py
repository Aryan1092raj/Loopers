"""GPU worker on Modal: serves api.main:app (IS_WORKER=1) in place of the Lightning Studio + ngrok.

One-time setup, from RAG_DB:
  modal secret create loopers-worker WORKER_KEY=... QDRANT_URL=... QDRANT_API_KEY=... DEEPSEEK_API_KEY=... HF_TOKEN=...
  modal volume create loopers-data
  modal volume put loopers-data deploy_data/metadata /metadata
  modal volume put loopers-data deploy_data/processed /processed
  modal volume put loopers-data deploy_data/raw /raw
Deploy:  modal deploy modal_worker.py      (WARM=1 modal deploy modal_worker.py keeps one container always on)
The printed URL is the worker's WORKER_URL on Render and MEDIA_URL on Vercel. WORKER_KEY must match Render and Vercel.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import modal

HERE = Path(__file__).parent
DATA = "/data"

app = modal.App("loopers-worker")
volume = modal.Volume.from_name("loopers-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0", "libgomp1", "curl")
    .pip_install_from_requirements(str(HERE / "requirements.app.txt"))
    .env({
        "IS_WORKER": "1",
        "LOOPERS_NO_REEXEC": "1",
        "MPLBACKEND": "Agg",
        "PYTHONUNBUFFERED": "1",
        "COLLECTION": "multimodal_hackathon",
        "EMBED_MODEL": "google/embeddinggemma-2",
        "WHISPER_DEVICE": "cuda",
        "WHISPER_COMPUTE_TYPE": "float16",
        "HF_HOME": f"{DATA}/hf",
    })
    .add_local_dir(HERE / "src", "/app/src", ignore=["**/__pycache__"])
    .add_local_dir(HERE / "templates", "/app/templates")
)


def _commit_loop():
    # ponytail: blind commit every 20 s so uploads and job output survive a container restart; add a lock if it ever races a write
    while True:
        time.sleep(20)
        try:
            volume.commit()
        except Exception as e:
            print(f"[modal] volume commit failed: {e}", flush=True)


@app.function(
    image=image,
    gpu="T4",
    volumes={DATA: volume},
    secrets=[modal.Secret.from_name("loopers-worker")],
    timeout=3600,
)
def reindex(source_ids: list[str]) -> dict:
    """Re-embed the units already in the volume's course.db for these sources and upsert them into Qdrant.

    Point ids come from the unit id, so a rerun overwrites the same points and never touches other sources.
    """
    import shutil
    import sqlite3

    sys.path.insert(0, "/app/src")
    volume.reload()
    work = Path("/tmp/reindex")
    work.mkdir(exist_ok=True)
    for ext in ("", "-wal", "-shm"):  # copy so reading never writes WAL files onto the live volume
        src = Path(f"{DATA}/metadata/course.db{ext}")
        if src.exists():
            shutil.copy(src, work / src.name)
    conn = sqlite3.connect(work / "course.db")
    conn.row_factory = sqlite3.Row
    import embeddings
    import store
    from config import COLLECTION

    client = store.client()
    store.ensure_indexes(client)
    done = {}
    for sid in source_ids:
        src = conn.execute("SELECT kind, title FROM sources WHERE source_id = ?", (sid,)).fetchone()
        rows = conn.execute("SELECT * FROM units WHERE source_id = ?", (sid,)).fetchall()
        if src is None or not rows:
            done[sid] = 0
            continue
        concepts = {}
        for u, c in conn.execute("SELECT unit_id, concept_id FROM unit_concepts"):
            concepts.setdefault(u, []).append(c)
        for i in range(0, len(rows), 32):
            batch = rows[i:i + 32]
            dense, sparse = embeddings.encode_hybrid([r["text"] for r in batch])
            payloads = [{
                "unit_id": r["unit_id"], "source_id": sid, "source_type": src["kind"], "kind": r["kind"],
                "title": src["title"], "heading": r["heading"], "text": r["text"], "parent_text": None,
                "page_no": r["page_no"], "slide_no": r["slide_no"], "t_start": r["t_start"], "t_end": r["t_end"],
                "matched_slide_page": r["matched_slide_page"], "image": r["image_path"],
                "latex": json.loads(r["latex_json"] or "[]"), "lang": r["lang"], "topic_id": r["topic_id"],
                "concept_ids": concepts.get(r["unit_id"], []), "has_math": bool(r["has_math"]),
            } for r in batch]
            store.upsert(client, [r["unit_id"] for r in batch], dense, payloads, sparse)
        done[sid] = len(rows)
        print(f"[reindex] {sid}: {len(rows)} units", flush=True)
    print(f"[reindex] collection {COLLECTION}: {client.count(COLLECTION).count} points", flush=True)
    return done


@app.local_entrypoint()
def reindex_cli(source_ids: str):
    print(reindex.remote(source_ids.split(",")))


@app.function(
    image=image,
    gpu="T4",
    volumes={DATA: volume},
    secrets=[modal.Secret.from_name("loopers-worker")],
    max_containers=1,  # state is SQLite + files on one volume, so never run two containers
    min_containers=int(os.environ.get("WARM", "0")),
    scaledown_window=600,
    timeout=3600,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    # config.ROOT is /app, so point its data folders at the volume before the app imports
    for d in ("raw", "processed", "metadata"):
        os.makedirs(f"{DATA}/{d}", exist_ok=True)
        if not os.path.lexists(f"/app/{d}"):
            os.symlink(f"{DATA}/{d}", f"/app/{d}")
    sys.path.insert(0, "/app/src")
    os.environ.pop("WORKER_URL", None)  # the worker runs models locally
    threading.Thread(target=_commit_loop, daemon=True).start()
    from api.main import app as api_app

    return api_app
