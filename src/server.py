"""Built-in HTTP server providing a Web UI for the LMS pilot pipeline.

Run with:
    python src/server.py
Then open http://localhost:8000 in your browser.
"""
import config
import http.server
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

# Paths
ROOT = Path(__file__).resolve().parent.parent

# Add venv site-packages if running outside venv
venv_dir = ROOT / ".venv"
if venv_dir.exists():
    import glob
    for p in glob.glob(str(venv_dir / "lib" / "python*" / "site-packages")):
        if p not in sys.path:
            sys.path.insert(0, p)

RAW = ROOT / "raw"
PROCESSED = ROOT / "processed"
METADATA = ROOT / "metadata"
OUTPUT = ROOT / "output"
REGISTRY = METADATA / "registry.json"
LABELS = METADATA / "labels.json"
ENV_FILE = ROOT / ".env"

# Task execution state
task_lock = threading.Lock()
current_task = {
    "name": None,
    "status": "idle", # "idle", "running", "success", "failed"
    "logs": [],
    "chat_result": None,
    "start_time": None
}
task_thread = None

# ---------------------------------------------------------------------------
# In-process generate runner (keeps BGE-M3 warm via lru_cache)
# ---------------------------------------------------------------------------

_thread_local_capture = threading.local()


class ThreadLogCapture:
    """Captures stdout into the task log buffer (thread-local)."""
    def write(self, s: str) -> int:
        if s:
            with task_lock:
                current_task["logs"].append(s)
        return len(s)
    def flush(self):
        pass


class ThreadLocalStdoutProxy:
    def __init__(self, original):
        self.original = original

    def write(self, s):
        capture = getattr(_thread_local_capture, "buffer", None)
        if capture is not None:
            capture.write(s)
        else:
            self.original.write(s)

    def flush(self):
        capture = getattr(_thread_local_capture, "buffer", None)
        if capture is not None:
            capture.flush()
        else:
            self.original.flush()

# A print() from a worker thread that never set up the thread-local capture
# buffer (e.g. a ThreadPoolExecutor task) falls through to `self.original` —
# the real console stream, which on Windows defaults to cp1252 and can't
# encode non-Latin-1 characters (Greek letters, arrows, etc. the LLM often
# writes). That crashed whole generation runs with "'charmap' codec can't
# encode character...". Force UTF-8 with lossy-but-non-fatal replacement on
# the real streams before wrapping them, so no thread can hit this crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass  # non-reconfigurable stream (e.g. already redirected to a pipe)

# Replace sys.stdout/sys.stderr globally with the thread-local proxy at startup
sys.stdout = ThreadLocalStdoutProxy(sys.stdout)
sys.stderr = ThreadLocalStdoutProxy(sys.stderr)


def run_generate_inprocess(query: str, subject: str | None = None, grade_level: str | None = None) -> None:
    """Run generate.run() in-process so the BGE-M3 model stays warm.

    stdout/stderr are redirected thread-locally to the task log buffer.
    """
    global current_task
    with task_lock:
        current_task["name"] = f"generate {query}"
        current_task["status"] = "running"
        current_task["start_time"] = time.time()
        current_task["logs"] = ["[System] Starting phase: generate...\n"]

    class ThreadLogCapture:
        def write(self, s: str) -> int:
            if s:
                with task_lock:
                    current_task["logs"].append(s)
            return len(s)
        def flush(self):
            pass

    _thread_local_capture.buffer = ThreadLogCapture()
    import cost_logger
    import cancel_flag
    cost_logger.reset()
    cancel_flag.reset()
    ok, error = False, None
    try:
        # Ensure src/ is on the path so local imports work
        src_dir = str(Path(__file__).resolve().parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        import generate as _generate
        _generate.run(query, subject=subject, grade_level=grade_level)
        ok = True
        with task_lock:
            current_task["status"] = "success"
            current_task["logs"].append("\n[System] Phase generate completed successfully!\n")
    except cancel_flag.GenerationCancelled:
        with task_lock:
            current_task["status"] = "cancelled"
            current_task["logs"].append("\n[System] Generation stopped by user.\n")
    except SystemExit as e:
        error = str(e)
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Phase generate exited: {e}\n")
    except Exception as e:
        error = str(e)
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Phase generate error: {e}\n")
    finally:
        _thread_local_capture.buffer = None
        cost = cost_logger.write_log("generate", query, subject, ok, error)
        with task_lock:
            current_task["cost"] = cost
            current_task["logs"].append(
                f"\n[Cost] {cost['tokens']['totalInput']} in / {cost['tokens']['totalOutput']} out tokens "
                f"— ${cost['totalCost']['usd']:.4f} (₹{cost['totalCost']['inr']:.2f})\n"
            )


def run_chat_inprocess(message: str) -> None:
    """Run chat.handle_chat() in-process with thread-local log capture."""
    global current_task
    with task_lock:
        current_task["name"] = f"chat: {message[:60]}"
        current_task["status"] = "running"
        current_task["logs"] = ["[System] Processing chat request...\n"]
        current_task["chat_result"] = None

    class ThreadLogCapture:
        def write(self, s: str) -> int:
            if s:
                with task_lock:
                    current_task["logs"].append(s)
            return len(s)
        def flush(self):
            pass

    _thread_local_capture.buffer = ThreadLogCapture()
    try:
        src_dir = str(Path(__file__).resolve().parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import chat as _chat
        result = _chat.handle_chat(message)
        with task_lock:
            current_task["status"] = "success" if result.get("status") == "ok" else "failed"
            current_task["chat_result"] = result
            current_task["logs"].append(f"\n[System] Chat completed: {result}\n")
    except Exception as e:
        with task_lock:
            current_task["status"] = "failed"
            current_task["chat_result"] = {"status": "error", "message": str(e)}
            current_task["logs"].append(f"\n[System] Chat error: {e}\n")
    finally:
        _thread_local_capture.buffer = None


def run_notes_inprocess(query: str, subject: str | None = None) -> None:
    global current_task
    with task_lock:
        current_task["name"] = f"notes {query}"
        current_task["status"] = "running"
        current_task["start_time"] = time.time()
        current_task["logs"] = ["[System] Starting phase: notes...\n"]

    class ThreadLogCapture:
        def write(self, s: str) -> int:
            if s:
                with task_lock:
                    current_task["logs"].append(s)
            return len(s)
        def flush(self):
            pass

    _thread_local_capture.buffer = ThreadLogCapture()
    import cost_logger
    import cancel_flag
    cost_logger.reset()
    cancel_flag.reset()
    ok, error = False, None
    try:
        src_dir = str(Path(__file__).resolve().parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import notes as _notes
        _notes.run(query, subject=subject)
        ok = True
        with task_lock:
            current_task["status"] = "success"
            current_task["logs"].append("\n[System] Phase notes completed successfully!\n")
    except cancel_flag.GenerationCancelled:
        with task_lock:
            current_task["status"] = "cancelled"
            current_task["logs"].append("\n[System] Generation stopped by user.\n")
    except Exception as e:
        error = str(e)
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Phase notes error: {e}\n")
    finally:
        _thread_local_capture.buffer = None
        cost = cost_logger.write_log("notes", query, subject, ok, error)
        with task_lock:
            current_task["cost"] = cost
            current_task["logs"].append(
                f"\n[Cost] {cost['tokens']['totalInput']} in / {cost['tokens']['totalOutput']} out tokens "
                f"— ${cost['totalCost']['usd']:.4f} (₹{cost['totalCost']['inr']:.2f})\n"
            )


def run_shorts_inprocess(payload: dict) -> None:
    """Condense a course's already-generated lecture notes into one revision PDF.

    Runs in a worker thread because HTTPServer here is single-threaded — a
    synchronous handler would block every other request for the whole run.
    """
    global current_task
    course = payload.get("course") or {}
    cutoff = payload.get("cutoff") or ""
    lectures = payload.get("lectures") or []
    label = f"{course.get('title') or 'course'} till {cutoff}"

    with task_lock:
        current_task["name"] = f"shorts {label}"
        current_task["status"] = "running"
        current_task["start_time"] = time.time()
        current_task["logs"] = [f"[System] Starting phase: shorts ({len(lectures)} lectures)...\n"]
        current_task["shorts_result"] = None

    class ThreadLogCapture:
        def write(self, s: str) -> int:
            if s:
                with task_lock:
                    current_task["logs"].append(s)
            return len(s)
        def flush(self):
            pass

    _thread_local_capture.buffer = ThreadLogCapture()
    import cost_logger
    import cancel_flag
    cost_logger.reset()
    cancel_flag.reset()
    ok, error, result = False, None, None
    try:
        src_dir = str(Path(__file__).resolve().parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import shorts as _shorts
        result = _shorts.generate(course, cutoff, lectures)
        ok = True
        with task_lock:
            current_task["status"] = "success"
            current_task["logs"].append("\n[System] Phase shorts completed successfully!\n")
    except cancel_flag.GenerationCancelled:
        error = "cancelled"
        with task_lock:
            current_task["status"] = "cancelled"
            current_task["logs"].append("\n[System] Generation stopped by user.\n")
    except Exception as e:
        error = str(e)
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Phase shorts error: {e}\n")
    finally:
        _thread_local_capture.buffer = None
        cost = cost_logger.write_log("shorts", label, None, ok, error)
        with task_lock:
            current_task["cost"] = cost
            current_task["shorts_result"] = {
                "status": "ok" if ok else "error",
                "error": error,
                "pdf": (result or {}).get("pdf"),
                "topics": (result or {}).get("topics", 0),
                "calls": (result or {}).get("calls", 0),
                "usage": {
                    "provider": "deepseek",
                    "model": config.SHORTS_MODEL,
                    "tokensIn": cost["tokens"]["totalInput"],
                    "tokensOut": cost["tokens"]["totalOutput"],
                    "costUsd": cost["totalCost"]["usd"],
                },
            }
            current_task["logs"].append(
                f"\n[Cost] {cost['tokens']['totalInput']} in / {cost['tokens']['totalOutput']} out tokens "
                f"— ${cost['totalCost']['usd']:.4f} (₹{cost['totalCost']['inr']:.2f})\n"
            )


def run_assignment_inprocess(query: str, subject: str | None = None, assignment_type: str | None = None, question_count: int | None = None) -> None:
    global current_task
    with task_lock:
        current_task["name"] = f"assignment {query}"
        current_task["status"] = "running"
        current_task["start_time"] = time.time()
        current_task["logs"] = ["[System] Starting phase: assignment...\n"]

    class ThreadLogCapture:
        def write(self, s: str) -> int:
            if s:
                with task_lock:
                    current_task["logs"].append(s)
            return len(s)
        def flush(self):
            pass

    _thread_local_capture.buffer = ThreadLogCapture()
    import cost_logger
    import cancel_flag
    cost_logger.reset()
    cancel_flag.reset()
    ok, error = False, None
    try:
        src_dir = str(Path(__file__).resolve().parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import assignment as _assignment
        _assignment.run(query, subject=subject, assignment_type=assignment_type, question_count=question_count)
        ok = True
        with task_lock:
            current_task["status"] = "success"
            current_task["logs"].append("\n[System] Phase assignment completed successfully!\n")
    except cancel_flag.GenerationCancelled:
        with task_lock:
            current_task["status"] = "cancelled"
            current_task["logs"].append("\n[System] Generation stopped by user.\n")
    except Exception as e:
        error = str(e)
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Phase assignment error: {e}\n")
    finally:
        _thread_local_capture.buffer = None
        cost = cost_logger.write_log("assignment", query, subject, ok, error)
        with task_lock:
            current_task["cost"] = cost
            current_task["logs"].append(
                f"\n[Cost] {cost['tokens']['totalInput']} in / {cost['tokens']['totalOutput']} out tokens "
                f"— ${cost['totalCost']['usd']:.4f} (₹{cost['totalCost']['inr']:.2f})\n"
            )


def run_process_document_inprocess(rel_path: str, subject: str) -> None:
    global current_task
    with task_lock:
        current_task["name"] = f"ingest-doc {rel_path}"
        current_task["status"] = "running"
        current_task["start_time"] = time.time()
        current_task["logs"] = [f"[System] Starting document processing for: {rel_path}...\n"]

    class ThreadLogCapture:
        def write(self, s: str) -> int:
            if s:
                with task_lock:
                    current_task["logs"].append(s)
            return len(s)
        def flush(self):
            pass

    _thread_local_capture.buffer = ThreadLogCapture()
    try:
        # Ensure src/ is on the path so local imports work
        src_dir = str(Path(__file__).resolve().parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        import ingest
        import extract
        import label
        import embed

        print(f"[System] Phase 1: Ingesting file...")
        ingest.run()

        print(f"[System] Phase 2: Extracting file content...")
        extract.run()

        print(f"[System] Phase 3: Labeling figure/math elements...")
        label.run()

        print(f"[System] Phase 4: Encoding and embedding chunks...")
        embed.run()

        with task_lock:
            current_task["status"] = "success"
            current_task["logs"].append(f"\n[System] Document {rel_path} successfully processed and embedded into Qdrant!\n")
    except Exception as e:
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Error processing document: {e}\n")
    finally:
        _thread_local_capture.buffer = None


def get_venv_python() -> str:
    # Use python in venv if present, otherwise fallback to current sys.executable
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable

def run_pipeline_command(phase: str, args_list: list = None) -> None:
    global current_task
    with task_lock:
        current_task["name"] = f"{phase} {' '.join(args_list) if args_list else ''}".strip()
        current_task["status"] = "running"
        current_task["start_time"] = time.time()
        current_task["logs"] = [f"[System] Starting phase: {phase}...\n"]

    python_bin = get_venv_python()
    cmd = [python_bin, str(ROOT / "src" / "pipeline.py"), phase]
    if args_list:
        cmd.extend(args_list)

    try:
        # Run process and capture stdout/stderr in real time
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        while True:
            line = process.stdout.readline()
            if not line:
                break
            with task_lock:
                current_task["logs"].append(line)
        
        process.wait()
        
        with task_lock:
            if process.returncode == 0:
                current_task["status"] = "success"
                current_task["logs"].append(f"\n[System] Phase {phase} completed successfully!\n")
            else:
                current_task["status"] = "failed"
                current_task["logs"].append(f"\n[System] Phase {phase} failed with exit code {process.returncode}\n")
                
    except Exception as e:
        with task_lock:
            current_task["status"] = "failed"
            current_task["logs"].append(f"\n[System] Error running task: {e}\n")

class PipelineHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default server logging to console to keep terminal clean
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_get_dashboard_html().encode("utf-8"))
            return

        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            # Read registry
            registry = {}
            if REGISTRY.exists():
                try:
                    registry = json.loads(REGISTRY.read_text())
                except Exception:
                    pass

            # Read labels
            labels = {}
            if LABELS.exists():
                try:
                    labels = json.loads(LABELS.read_text())
                except Exception:
                    pass

            # Read raw folder
            raw_files = []
            if RAW.exists():
                raw_files = [p.name for p in RAW.glob("*") if p.is_file() and p.name != ".gitkeep"]

            # Read Groq and Nvidia API Key states
            has_groq_key = False
            groq_key_val = ""
            has_nvidia_key = False
            nvidia_key_val = ""
            if ENV_FILE.exists():
                lines = ENV_FILE.read_text().splitlines()
                for line in lines:
                    if line.startswith("GROQ_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val and val != "YOUR_GROQ_KEY":
                            has_groq_key = True
                            groq_key_val = val[:10] + "..." if len(val) > 10 else "***"
                    elif line.startswith("NVIDIA_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val and val != "YOUR_NVIDIA_KEY":
                            has_nvidia_key = True
                            nvidia_key_val = val[:10] + "..." if len(val) > 10 else "***"

            # Read output files — only user-facing deliverables
            _DELIVERABLE_NAMES = frozenset([
                "student_notes.pdf",
                "assignment_student.pdf", "assignment_student_teacher.pdf",
            ])
            output_files = []
            if OUTPUT.exists():
                # Scan flat files + one level of subdirectories (query folders)
                candidates = list(OUTPUT.glob("*")) + list(OUTPUT.glob("*/*"))
                task_start = current_task.get("start_time")
                for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
                    if p.is_file() and p.suffix in (".pdf", ".pptx", ".json") and p.name != ".gitkeep" and not p.name.startswith("_"):
                        # Only surface files produced by the current task, not leftovers from earlier runs
                        if task_start is not None and p.stat().st_mtime < task_start - 2:
                            continue
                        rel = p.relative_to(OUTPUT)
                        output_files.append({"name": rel.as_posix(), "size": p.stat().st_size, "url": f"/output/{rel.as_posix()}"})

            with task_lock:
                status_payload = {
                    "registry": registry,
                    "labels_count": len(labels),
                    "raw_files": raw_files,
                    "output_files": output_files,
                    "has_groq_key": has_groq_key,
                    "groq_key_preview": groq_key_val,
                    "has_nvidia_key": has_nvidia_key,
                    "nvidia_key_preview": nvidia_key_val,
                    "current_task": {
                        "name": current_task["name"],
                        "status": current_task["status"],
                        "logs": current_task["logs"],
                        "cost": current_task.get("cost"),
                    }
                }
            
            self.wfile.write(json.dumps(status_payload).encode("utf-8"))
            return

        elif path == "/api/reviewer/queue":
            try:
                # Read review status
                status_file = METADATA / "review_status.json"
                statuses = {}
                if status_file.exists():
                    try:
                        statuses = json.loads(status_file.read_text())
                    except Exception:
                        pass

                concepts = []
                concepts_dir = PROCESSED / "concepts"
                if concepts_dir.exists():
                    for d in sorted(concepts_dir.iterdir()):
                        if d.is_dir() and not d.name.startswith("."):
                            # Read metadata/content
                            content_file = d / "content.md"
                            align_file = d / "alignment.json"
                            assign_file = d / "assignments.json"

                            content = content_file.read_text() if content_file.exists() else ""
                            alignments = []
                            if align_file.exists():
                                try:
                                    alignments = json.loads(align_file.read_text())
                                except Exception:
                                    pass
                            assignments = []
                            if assign_file.exists():
                                try:
                                    assignments = json.loads(assign_file.read_text())
                                except Exception:
                                    pass

                            concepts.append({
                                "slug": d.name,
                                "status": statuses.get(d.name, "pending"),
                                "content": content,
                                "alignments": alignments,
                                "assignments": assignments
                            })

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"queue": concepts}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        elif path == "/api/embeddings":
            query_params = urllib.parse.parse_qs(url.query)
            limit = int(query_params.get("limit", [50])[0])
            offset = query_params.get("offset", [None])[0]
            if offset == "None" or offset == "":
                offset = None

            try:
                import store
                from config import COLLECTION
                c = store.client()
                points, next_offset = c.scroll(
                    collection_name=COLLECTION,
                    limit=limit,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )
                
                chunks = []
                for p in points:
                    chunks.append({
                        "id": str(p.id),
                        "payload": p.payload
                    })
                
                res_data = {
                    "chunks": chunks,
                    "next_offset": str(next_offset) if next_offset else None,
                    "total": store.count(c)
                }
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res_data).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        elif path == "/api/subjects":
            try:
                from config import discover_subjects
                subjects = discover_subjects()
                # Count files per subject
                subject_counts = {}
                for subj in subjects:
                    if subj == "general":
                        count = sum(1 for f in RAW.iterdir() if f.is_file() and not f.name.startswith("."))
                    else:
                        subj_dir = RAW / subj
                        count = sum(1 for f in subj_dir.rglob("*") if f.is_file()) if subj_dir.exists() else 0
                    subject_counts[subj] = count
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"subjects": subjects, "counts": subject_counts}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        elif path == "/api/chat_result":
            with task_lock:
                result = current_task.get("chat_result")
                status = current_task["status"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": status, "result": result}).encode("utf-8"))
            return

        elif path == "/api/shorts_result":
            with task_lock:
                result = current_task.get("shorts_result")
                status = current_task["status"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": status, "result": result}).encode("utf-8"))
            return

        elif path == "/api/embeddings/stats":
            query_params = urllib.parse.parse_qs(url.query)
            subject_filter = query_params.get("subject", [None])[0]
            try:
                import store
                from config import COLLECTION, VECTOR_SIZE
                from collections import Counter
                
                c = store.client()
                total = store.count(c)
                
                all_payloads = []
                offset = None
                while True:
                    points, offset = c.scroll(
                        collection_name=COLLECTION,
                        limit=200,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False
                    )
                    all_payloads.extend([p.payload for p in points])
                    if offset is None:
                        break
                
                # Apply subject filter if specified
                if subject_filter and subject_filter != "all":
                    all_payloads = [p for p in all_payloads if p.get("subject") == subject_filter]
                
                by_doc = Counter(p.get("doc", "?") for p in all_payloads)
                by_kind = Counter(p.get("kind", "?") for p in all_payloads)
                by_subject = Counter(p.get("subject", "?") for p in all_payloads)
                
                stats = {
                    "total_chunks": len(all_payloads) if subject_filter and subject_filter != "all" else total,
                    "vector_dim": VECTOR_SIZE,
                    "by_document": dict(by_doc),
                    "by_kind": dict(by_kind),
                    "by_subject": dict(by_subject),
                    "has_math": sum(1 for p in all_payloads if p.get("has_math")),
                    "has_table": sum(1 for p in all_payloads if p.get("has_table")),
                    "has_tikz": sum(1 for p in all_payloads if p.get("has_tikz"))
                }
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(stats).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Error fetching stats: {e}"}).encode("utf-8"))
            return

        elif path == "/api/embeddings/search":
            query_params = urllib.parse.parse_qs(url.query)
            q = query_params.get("q", [""])[0]
            k = int(query_params.get("k", [5])[0])
            subject_filter = query_params.get("subject", [None])[0]
            
            if not q:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing query parameter 'q'"}).encode("utf-8"))
                return
                
            try:
                import store
                from embeddings import encode_hybrid
                dense_vecs, sparse_vecs = encode_hybrid(q)
                qvec = dense_vecs[0]
                qsparse = sparse_vecs[0] if sparse_vecs else None
                subj = subject_filter if subject_filter and subject_filter != "all" else None
                results = store.query(store.client(), qvec, k=k, subject=subj, sparse_vector=qsparse)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"query": q, "results": results}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Error searching embeddings: {e}"}).encode("utf-8"))
            return

        elif path == "/api/corpus/catalog":
            try:
                from corpus_downloader import OPENSTAX_CATALOG, NCERT_SUBJECTS
                catalog = {
                    "openstax": list(OPENSTAX_CATALOG.keys()),
                    "ncert": list(NCERT_SUBJECTS.keys()),
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(catalog).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        elif path == "/api/settings":
            import os as _os
            settings = {
                "CLAF_TAGGING": _os.getenv("CLAF_TAGGING", "0") == "1",
                "KG_EXTRACTION": _os.getenv("KG_EXTRACTION", "0") == "1",
                "CHUNK_WORDS": int(_os.getenv("CHUNK_WORDS", "350")),
                "SCORE_THRESHOLD": float(_os.getenv("SCORE_THRESHOLD", "0.30")),
                "OCR_ENGINE": _os.getenv("OCR_ENGINE", "paddle"),
                "GLM_OCR_MODE": _os.getenv("GLM_OCR_MODE", "local"),
                "GLM_OCR_API_URL": _os.getenv("GLM_OCR_API_URL", "http://localhost:11434/v1"),
                "GLM_OCR_API_KEY": _os.getenv("GLM_OCR_API_KEY", "EMPTY"),
                "GLM_OCR_MODEL": _os.getenv("GLM_OCR_MODEL", "zai-org/GLM-OCR"),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(settings).encode("utf-8"))
            return

        elif path.startswith("/output/"):
            rel_path = path.lstrip("/")
            file_path = ROOT / rel_path
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                _MIME = {
                    ".pdf": "application/pdf",
                    ".json": "application/json",
                    ".wav": "audio/wav",
                    ".mp3": "audio/mpeg",
                    ".txt": "text/plain; charset=utf-8",
                    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                }
                self.send_header("Content-Type", _MIME.get(file_path.suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(file_path.stat().st_size))
                self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
                return

        elif path == "/view_spec":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(VIEW_SPEC_HTML.encode("utf-8"))
            return

        elif path.startswith("/processed/images/"):
            rel_path = path.lstrip("/")
            file_path = ROOT / rel_path
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(file_path.stat().st_size))
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
                return

        # Serve static assets or return 404
        self.send_error(404, "File not found")

    def do_POST(self):
        global task_thread
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path == "/api/run":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            phase = data.get("phase")
            args_list = data.get("args", [])
            theme = data.get("theme", "cream")
            os.environ["SLIDE_THEME"] = theme

            if not phase:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing phase parameter")
                return

            with task_lock:
                if current_task["status"] == "running":
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Another task is already running")
                    return

            # generate runs in-process so BGE-M3 stays warm (lru_cache)
            if phase in ("notes", "assignment"):
                subject = None
                assignment_type = None
                question_count = None
                clean_args = []
                i = 0
                while i < len(args_list):
                    arg = args_list[i]
                    if arg == "--subject" and i + 1 < len(args_list):
                        subject = args_list[i + 1]
                        i += 2
                    elif arg == "--type" and i + 1 < len(args_list):
                        assignment_type = args_list[i + 1]
                        i += 2
                    elif arg == "--count" and i + 1 < len(args_list):
                        try:
                            question_count = int(args_list[i + 1])
                        except ValueError:
                            question_count = None
                        i += 2
                    else:
                        clean_args.append(arg)
                        i += 1
                args_list = clean_args

                # Also get from JSON data directly if present
                if not assignment_type:
                    assignment_type = data.get("assignment_type")
                if not question_count:
                    try:
                        question_count = int(data.get("question_count")) if data.get("question_count") is not None else None
                    except ValueError:
                        pass

                query = " ".join(args_list)
                if phase == "notes":
                    task_thread = threading.Thread(target=run_notes_inprocess, args=(query, subject), daemon=True)
                else:
                    task_thread = threading.Thread(
                        target=run_assignment_inprocess,
                        args=(query, subject, assignment_type, question_count),
                        daemon=True
                    )
            else:
                task_thread = threading.Thread(
                    target=run_pipeline_command,
                    args=(phase, args_list),
                    daemon=True
                )
            task_thread.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        elif path == "/api/cancel":
            # Cooperative stop: sets a flag every LLM call site checks before
            # firing (see cancel_flag.py). Takes effect within roughly one
            # in-flight call, not instantly — a blocking HTTP request already
            # sent to Anthropic/Groq/etc. can't be aborted mid-flight.
            import cancel_flag
            with task_lock:
                already_done = current_task["status"] != "running"
            if already_done:
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No generation is currently running"}).encode("utf-8"))
                return
            cancel_flag.request_cancel()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "cancelling"}).encode("utf-8"))
            return

        elif path == "/api/save_spec":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid JSON body")
                return

            filename = data.get("file")
            spec = data.get("spec")
            if not filename or not spec:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing file or spec")
                return

            file_path = (OUTPUT / filename).resolve()
            if not file_path.is_relative_to(OUTPUT):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Access denied")
                return

            # Validate against schema
            try:
                from jsonschema import validate
                import generate as _generate
                validate(spec, _generate.SCHEMA)
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Schema validation failed: {e}".encode("utf-8"))
                return

            # Save and Render PDF
            try:
                file_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
                
                # Render PDF
                from render_latex import render as _render_latex
                pdf_path = OUTPUT / (file_path.stem + ".pdf")
                _render_latex(spec, pdf_path)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Error rendering: {e}".encode("utf-8"))
                return

        elif path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            message = data.get("message", "").strip()
            if not message:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing message")
                return
            with task_lock:
                if current_task["status"] == "running":
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Another task is running")
                    return
            task_thread = threading.Thread(
                target=run_chat_inprocess, args=(message,), daemon=True
            )
            task_thread.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        elif path == "/api/shorts":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            lectures = data.get("lectures") or []
            if not data.get("cutoff") or not lectures:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing cutoff or lectures")
                return
            with task_lock:
                if current_task["status"] == "running":
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Another task is running")
                    return
            task_thread = threading.Thread(
                target=run_shorts_inprocess, args=(data,), daemon=True
            )
            task_thread.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        elif path == "/api/update_env":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            groq_key = data.get("groq_key")
            nvidia_key = data.get("nvidia_key")
            if not groq_key and not nvidia_key:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing groq_key or nvidia_key")
                return

            # Read existing .env
            env_content = ""
            if ENV_FILE.exists():
                env_content = ENV_FILE.read_text()

            lines = env_content.splitlines()

            if groq_key:
                updated = False
                for i, line in enumerate(lines):
                    if line.startswith("GROQ_API_KEY="):
                        lines[i] = f"GROQ_API_KEY={groq_key}"
                        updated = True
                        break
                if not updated:
                    lines.insert(0, f"GROQ_API_KEY={groq_key}")
                os.environ["GROQ_API_KEY"] = groq_key
                import config
                config.GROQ_API_KEY = groq_key
                config.GROQ_KEYS = [k.strip() for k in groq_key.split(",")] if groq_key else []

            if nvidia_key:
                updated = False
                for i, line in enumerate(lines):
                    if line.startswith("NVIDIA_API_KEY="):
                        lines[i] = f"NVIDIA_API_KEY={nvidia_key}"
                        updated = True
                        break
                if not updated:
                    lines.insert(0, f"NVIDIA_API_KEY={nvidia_key}")
                os.environ["NVIDIA_API_KEY"] = nvidia_key
                import config
                config.NVIDIA_API_KEY = nvidia_key
                config.NVIDIA_KEYS = [k.strip() for k in nvidia_key.split(",")] if nvidia_key else []
            
            ENV_FILE.write_text("\n".join(lines) + "\n")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode("utf-8"))
            return

        elif path == "/api/reviewer/action":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            slug = data.get("concept")
            action = data.get("action")  # "approve" | "reject"

            if not slug or not action:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing concept or action")
                return

            try:
                # Update status file
                status_file = METADATA / "review_status.json"
                statuses = {}
                if status_file.exists():
                    try:
                        statuses = json.loads(status_file.read_text())
                    except Exception:
                        pass
                statuses[slug] = action
                METADATA.mkdir(parents=True, exist_ok=True)
                status_file.write_text(json.dumps(statuses, indent=2), encoding="utf-8")

                if action == "approve":
                    # Create combined markdown file
                    concept_dir = PROCESSED / "concepts" / slug
                    content_file = concept_dir / "content.md"
                    align_file = concept_dir / "alignment.json"
                    assign_file = concept_dir / "assignments.json"

                    md_content = content_file.read_text() if content_file.exists() else ""
                    
                    # Add alignments
                    if align_file.exists():
                        try:
                            alignments = json.loads(align_file.read_text())
                            if alignments:
                                md_content += "\n\n## Transcript Mappings and Visual Alignments\n"
                                for align in alignments:
                                    md_content += f"\n- **Transcript Snippet**: {align.get('transcript_snippet')}\n"
                                    md_content += f"  - **Context**: {align.get('context')}\n"
                                    md_content += f"  - **Presentation Type**: {align.get('presentation_type')}\n"
                                    md_content += f"  - **Visual Aid**: {align.get('visual_aids')}\n"
                        except Exception:
                            pass
                    
                    # Add assignments
                    if assign_file.exists():
                        try:
                            assignments = json.loads(assign_file.read_text())
                            if assignments:
                                md_content += "\n\n## Assignment Pool and Projects\n"
                                for assign in assignments:
                                    md_content += f"\n- **Question**: {assign.get('question')}\n"
                                    md_content += f"  - **Type**: {assign.get('type')}\n"
                                    md_content += f"  - **Solution**: {assign.get('solution')}\n"
                        except Exception:
                            pass

                    # Write combined to processed/concept_<slug>.md
                    out_path = PROCESSED / f"concept_{slug}.md"
                    out_path.write_text(md_content, encoding="utf-8")

                    # Trigger embed phase in background task
                    with task_lock:
                        if current_task["status"] == "running":
                            self.send_response(202)  # accepted but cannot run embed immediately
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "approved", "warning": "task_running"}).encode("utf-8"))
                            return
                            
                    # Run embed in background
                    def _embed_task():
                        global current_task
                        with task_lock:
                            current_task["name"] = f"embed approved concept {slug}"
                            current_task["status"] = "running"
                            current_task["logs"] = [f"[System] Embedding approved concept: {slug}...\n"]
                        try:
                            # Import embed and run
                            import embed
                            embed.run(force_full=False)
                            with task_lock:
                                current_task["status"] = "success"
                                current_task["logs"].append(f"\n[System] Concept {slug} successfully indexed!\n")
                        except Exception as e:
                            with task_lock:
                                current_task["status"] = "failed"
                                current_task["logs"].append(f"\n[System] Ingestion error: {e}\n")

                    task_thread = threading.Thread(target=_embed_task, daemon=True)
                    task_thread.start()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return

        elif path == "/api/corpus/download":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            source = data.get("source", "openstax")
            subjects = data.get("subjects", [])
            classes = data.get("classes", [10, 11, 12])
            target_subject = data.get("target_subject", "")
            if not subjects:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing subjects")
                return

            with task_lock:
                if current_task["status"] == "running":
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Another task is already running")
                    return

            def _download_task():
                global current_task
                old_stdout, old_stderr = sys.stdout, sys.stderr
                with task_lock:
                    current_task["name"] = f"download {source} {' '.join(subjects)}"
                    current_task["status"] = "running"
                    current_task["logs"] = [f"[System] Downloading from {source}...\n"]
                try:
                    cap = ThreadLogCapture()
                    sys.stdout = cap
                    sys.stderr = cap
                    from corpus_downloader import download_openstax, download_ncert
                    from config import subject_raw_dir
                    dest = subject_raw_dir(target_subject) if target_subject else RAW
                    if source == "openstax":
                        results = download_openstax(subjects, dest)
                    elif source == "ncert":
                        results = download_ncert(classes, subjects, dest)
                    else:
                        results = {}
                    ok = sum(1 for v in results.values() if v)
                    with task_lock:
                        current_task["status"] = "success"
                        current_task["logs"].append(f"\n[System] Download complete: {ok}/{len(results)} files\n")
                except Exception as e:
                    with task_lock:
                        current_task["status"] = "failed"
                        current_task["logs"].append(f"\n[System] Download error: {e}\n")
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr

            task_thread = threading.Thread(target=_download_task, daemon=True)
            task_thread.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        elif path == "/api/settings":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            import os as _os
            env_updates = []
            for key in ["CLAF_TAGGING", "KG_EXTRACTION", "CHUNK_WORDS", "SCORE_THRESHOLD", "OCR_ENGINE", "GLM_OCR_MODE", "GLM_OCR_API_URL", "GLM_OCR_API_KEY", "GLM_OCR_MODEL"]:
                if key in data:
                    val = str(data[key])
                    if isinstance(data[key], bool):
                        val = "1" if data[key] else "0"
                    _os.environ[key] = val
                    env_updates.append((key, val))
            # Persist to .env
            if env_updates and ENV_FILE.exists():
                lines = ENV_FILE.read_text().splitlines()
                for env_key, env_val in env_updates:
                    updated = False
                    for i, line in enumerate(lines):
                        if line.startswith(f"{env_key}="):
                            lines[i] = f"{env_key}={env_val}"
                            updated = True
                            break
                    if not updated:
                        lines.append(f"{env_key}={env_val}")
                ENV_FILE.write_text("\n".join(lines) + "\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode("utf-8"))
            return

        elif path == "/api/add_subject":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            source = data.get("source", "openstax")
            subjects = data.get("subjects", [])
            target_subject = data.get("target_subject", "")
            classes = data.get("classes", [10, 11, 12])
            if not subjects:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing subjects")
                return

            with task_lock:
                if current_task["status"] == "running":
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Another task is already running")
                    return

            def _add_subject_task():
                global current_task
                with task_lock:
                    current_task["name"] = f"add-subject {target_subject or source}"
                    current_task["status"] = "running"
                    current_task["logs"] = [f"[System] Adding subject: download → ingest → extract → label → embed\n"]
                cap = ThreadLogCapture()
                old_stdout, old_stderr = sys.stdout, sys.stderr
                sys.stdout = cap
                sys.stderr = cap
                try:
                    from corpus_downloader import download_openstax, download_ncert
                    from config import subject_raw_dir
                    dest = subject_raw_dir(target_subject) if target_subject else RAW
                    # Step 1: Download
                    if source == "openstax":
                        download_openstax(subjects, dest)
                    elif source == "ncert":
                        download_ncert(classes, subjects, dest)
                    # Step 2-5: Run full pipeline
                    from ingest import run as ingest_run
                    from extract import run as extract_run
                    from label import run as label_run
                    from embed import run as embed_run
                    ingest_run()
                    extract_run()
                    label_run()
                    embed_run()
                    with task_lock:
                        current_task["status"] = "success"
                        current_task["logs"].append("\n[System] Subject added successfully!\n")
                except Exception as e:
                    with task_lock:
                        current_task["status"] = "failed"
                        current_task["logs"].append(f"\n[System] Add subject error: {e}\n")
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr

            task_thread = threading.Thread(target=_add_subject_task, daemon=True)
            task_thread.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        elif path == "/api/upload":
            content_type = self.headers.get("Content-Type", "")
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            
            try:
                boundary = None
                if "boundary=" in content_type:
                    boundary = content_type.split("boundary=")[1].strip()
                    if boundary.startswith('"') and boundary.endswith('"'):
                        boundary = boundary[1:-1]
                
                if not boundary:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing boundary in Content-Type")
                    return
                
                boundary_bytes = b"--" + boundary.encode('utf-8')
                parts = body_bytes.split(boundary_bytes)
                
                saved_file = None
                for part in parts:
                    if not part or part.strip() == b"" or part.strip() == b"--":
                        continue
                    if b"\r\n\r\n" not in part:
                        continue
                    header_part, content = part.split(b"\r\n\r\n", 1)
                    if content.endswith(b"\r\n"):
                        content = content[:-2]
                    if content.startswith(b"\r\n"):
                        content = content[2:]
                        
                    header_text = header_part.decode('utf-8', errors='ignore')
                    import re
                    fn_match = re.search(r'filename="([^"]+)"', header_text, re.IGNORECASE)
                    if fn_match:
                        filename = fn_match.group(1)
                        filename = os.path.basename(filename)
                        if not filename:
                            continue
                        dest_path = RAW / filename
                        RAW.mkdir(parents=True, exist_ok=True)
                        dest_path.write_bytes(content)
                        saved_file = filename
                        break
                
                if saved_file:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "filename": saved_file}).encode("utf-8"))
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"No file found in upload request")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Upload error: {e}".encode("utf-8"))
            return

        elif path == "/api/upload_doc":
            content_type = self.headers.get("Content-Type", "")
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            
            try:
                boundary = None
                if "boundary=" in content_type:
                    boundary = content_type.split("boundary=")[1].strip()
                    if boundary.startswith('"') and boundary.endswith('"'):
                        boundary = boundary[1:-1]
                
                if not boundary:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing boundary in Content-Type")
                    return
                
                boundary_bytes = b"--" + boundary.encode('utf-8')
                parts = body_bytes.split(boundary_bytes)
                
                subject = "general"
                filename = None
                content = None
                run_pipeline = True
                
                for part in parts:
                     if not part or part.strip() == b"" or part.strip() == b"--":
                         continue
                     if b"\r\n\r\n" not in part:
                         continue
                     header_part, val = part.split(b"\r\n\r\n", 1)
                     if val.endswith(b"\r\n"):
                         val = val[:-2]
                     if val.startswith(b"\r\n"):
                         val = val[2:]
                         
                     header_text = header_part.decode('utf-8', errors='ignore')
                     import re
                     fn_match = re.search(r'filename="([^"]+)"', header_text, re.IGNORECASE)
                     name_match = re.search(r'name="([^"]+)"', header_text, re.IGNORECASE)
                     
                     if fn_match:
                         filename = os.path.basename(fn_match.group(1))
                         content = val
                     elif name_match and name_match.group(1) == "subject":
                         subject = val.decode('utf-8', errors='ignore').strip()
                     elif name_match and name_match.group(1) == "run_pipeline":
                         run_pipeline = val.decode('utf-8', errors='ignore').strip().lower() == "true"
                
                if not filename or content is None:
                     self.send_response(400)
                     self.end_headers()
                     self.wfile.write(b"No file found in upload request")
                     return
                 
                # Check for uniqueness using SHA-256 hash against metadata/registry.json
                import hashlib
                sha256_hash = hashlib.sha256(content).hexdigest()
                 
                # Load registry
                from ingest import load_registry
                registry = load_registry()
                 
                duplicate_key = None
                for key, entry in registry.items():
                     if entry.get("sha256") == sha256_hash:
                         duplicate_key = key
                         break
                 
                if duplicate_key:
                     self.send_response(400)
                     self.send_header("Content-Type", "application/json")
                     self.end_headers()
                     self.wfile.write(json.dumps({
                         "status": "duplicate",
                         "message": f"Duplicate file content detected. This file is already ingested as: {duplicate_key}"
                     }).encode("utf-8"))
                     return
                 
                # Verify that subject is canonical or general
                from config import KNOWN_SUBJECTS, subject_raw_dir
                if subject not in KNOWN_SUBJECTS and subject != "general":
                     subject = "general"
                 
                # Save file to subject raw directory
                target_dir = subject_raw_dir(subject)
                dest_path = target_dir / filename
                dest_path.write_bytes(content)
                 
                rel_path = str(dest_path.relative_to(RAW))
                 
                if run_pipeline:
                    # Start document processing task in background thread
                    with task_lock:
                        if current_task["status"] == "running":
                            self.send_response(409)
                            self.end_headers()
                            self.wfile.write(b"Another task is already running")
                            return
                    
                    task_thread = threading.Thread(
                        target=run_process_document_inprocess,
                        args=(rel_path, subject),
                        daemon=True
                    )
                    task_thread.start()
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "success",
                        "filename": filename,
                        "subject": subject
                    }).encode("utf-8"))
                else:
                    # Save file but do not start background task
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "success",
                        "message": "File saved successfully. Pipeline execution deferred.",
                        "filename": filename,
                        "subject": subject
                    }).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Upload error: {e}".encode("utf-8"))
            return

        elif path == "/api/transcribe":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
                
            source = data.get("source", "").strip()
            model_name = data.get("model", "whisper-v3-medium").strip()
            denoise = data.get("denoise", True)
            visual_align = data.get("visual_align", False)
            
            if not source:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing source URL or filepath")
                return
                
            with task_lock:
                if current_task["status"] == "running":
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Another task is already running")
                    return
            
            def _transcribe_task_wrapper():
                try:
                    from audio_processor import AudioProcessor
                    processor = AudioProcessor(OUTPUT)
                    
                    is_url = source.startswith("http://") or source.startswith("https://")
                    
                    with task_lock:
                        current_task["name"] = f"transcribe {source[:30]}"
                        current_task["status"] = "running"
                        current_task["logs"] = ["[System] Starting transcription task...\n"]
                        current_task["chat_result"] = None
                        
                    if is_url:
                        with task_lock:
                            current_task["logs"].append(f"[System] Downloading YouTube audio from: {source}\n")
                        audio_path = processor.download_youtube_audio(source)
                        with task_lock:
                            current_task["logs"].append(f"[System] Downloaded audio: {audio_path.name}\n")
                    else:
                        audio_path = Path(source)
                        if not audio_path.is_absolute():
                            audio_path = RAW / audio_path
                        if not audio_path.exists():
                            raise FileNotFoundError(f"Audio file not found: {audio_path}")
                        with task_lock:
                            current_task["logs"].append(f"[System] Using audio file: {audio_path.name}\n")
                            
                    cleaned_path = OUTPUT / f"cleaned_{audio_path.stem}.wav"
                    with task_lock:
                        current_task["logs"].append(f"[System] Cleaning/Converting audio (Denoise={denoise})...\n")
                    
                    success = processor.clean_audio(audio_path, cleaned_path, denoise=denoise)
                    if not success:
                        with task_lock:
                            current_task["logs"].append("[Warning] Cleaning/converting failed. Using original format.\n")
                        cleaned_path = audio_path
                    else:
                        with task_lock:
                            current_task["logs"].append(f"[System] Cleaned audio saved to: {cleaned_path.name}\n")
                            
                    with task_lock:
                        current_task["logs"].append(f"[System] Transcribing with {model_name}...\n")
                        
                    transcript = ""
                    if model_name.startswith("groq-"):
                        groq_key = os.getenv("GROQ_API_KEY")
                        if not groq_key or groq_key == "YOUR_GROQ_KEY":
                            raise ValueError("Groq API Key not configured. Please add it to your settings or environment.")
                        first_key = [k.strip() for k in groq_key.split(",")][0]
                        transcript = processor.transcribe_groq_whisper(cleaned_path, first_key)
                    elif model_name == "granite-speech":
                        try:
                            transcript = processor.transcribe_granite_speech(cleaned_path)
                        except Exception as e:
                            with task_lock:
                                current_task["logs"].append(f"[Warning] Granite Speech failed: {e}. Falling back to Whisper Medium.\n")
                            transcript = processor.transcribe_local_whisper(cleaned_path, "medium")
                    else:
                        size = "medium"
                        if "large" in model_name:
                            size = "large-v3"
                        transcript = processor.transcribe_local_whisper(cleaned_path, size)
                        
                    transcript_path = OUTPUT / f"transcript_{audio_path.stem}.txt"
                    transcript_path.write_text(transcript, encoding="utf-8")
                    
                    with task_lock:
                        current_task["logs"].append(f"[System] Transcript saved to: {transcript_path.name}\n")
                        
                    if visual_align:
                        with task_lock:
                            current_task["logs"].append("[System] Segmenting transcript into concepts...\n")
                        from process_transcripts import process_text_into_concepts, save_concepts
                        concepts = process_text_into_concepts(transcript)
                        saved_dirs = save_concepts(concepts, transcript_path.name)
                        with task_lock:
                            current_task["logs"].append(f"[System] Created {len(saved_dirs)} concept folders.\n")
                            
                    with task_lock:
                        current_task["status"] = "success"
                        current_task["chat_result"] = {
                            "transcript": transcript,
                            "transcript_file": transcript_path.name,
                            "cleaned_audio_file": cleaned_path.name if success else None,
                            "success": True
                        }
                        current_task["logs"].append("[System] Transcription completed successfully!\n")
                        
                except Exception as ex:
                    with task_lock:
                        current_task["status"] = "failed"
                        current_task["logs"].append(f"\n[Error] Transcription task failed: {ex}\n")
                        
            task_thread = threading.Thread(target=_transcribe_task_wrapper, daemon=True)
            task_thread.start()
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))
            return

        elif path == "/api/transcript/ingest":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            try:
                transcript_path_str = data.get("transcript_path")
                if transcript_path_str:
                    transcript_path = Path(transcript_path_str)
                    # If bare filename (no dir), resolve against OUTPUT
                    if not transcript_path.parent.name:
                        transcript_path = OUTPUT / transcript_path
                else:
                    # Find latest transcript in OUTPUT
                    txts = sorted(OUTPUT.glob("transcript_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if not txts:
                        raise FileNotFoundError("No transcript files found in output directory")
                    transcript_path = txts[0]

                if not transcript_path.exists():
                    raise FileNotFoundError(f"Transcript not found: {transcript_path}")

                # Run pipeline → returns list of concept dirs
                from process_transcripts import run_pipeline
                concept_dirs = run_pipeline(transcript_path)

                # Auto-approve: write combined concept_<slug>.md for each concept
                status_file = METADATA / "review_status.json"
                statuses = {}
                if status_file.exists():
                    try:
                        statuses = json.loads(status_file.read_text())
                    except Exception:
                        pass

                concept_names = []
                for concept_dir in concept_dirs:
                    slug = concept_dir.name
                    concept_names.append(slug)
                    statuses[slug] = "approve"

                    content_file = concept_dir / "content.md"
                    align_file = concept_dir / "alignment.json"
                    assign_file = concept_dir / "assignments.json"

                    md_content = content_file.read_text() if content_file.exists() else ""
                    if align_file.exists():
                        try:
                            alignments = json.loads(align_file.read_text())
                            if alignments:
                                md_content += "\n\n## Transcript Mappings and Visual Alignments\n"
                                for align in alignments:
                                    md_content += f"\n- **Transcript Snippet**: {align.get('transcript_snippet')}\n"
                                    md_content += f"  - **Context**: {align.get('context')}\n"
                                    md_content += f"  - **Presentation Type**: {align.get('presentation_type')}\n"
                                    md_content += f"  - **Visual Aid**: {align.get('visual_aids')}\n"
                        except Exception:
                            pass
                    if assign_file.exists():
                        try:
                            assignments = json.loads(assign_file.read_text())
                            if assignments:
                                md_content += "\n\n## Assignment Pool and Projects\n"
                                for assign in assignments:
                                    md_content += f"\n- **Question**: {assign.get('question')}\n"
                                    md_content += f"  - **Type**: {assign.get('type')}\n"
                                    md_content += f"  - **Solution**: {assign.get('solution')}\n"
                        except Exception:
                            pass

                    out_path = PROCESSED / f"concept_{slug}.md"
                    out_path.write_text(md_content, encoding="utf-8")

                METADATA.mkdir(parents=True, exist_ok=True)
                status_file.write_text(json.dumps(statuses, indent=2), encoding="utf-8")

                # Count chunks before embed
                chunks_before = len(list(PROCESSED.glob("*.md")))
                import embed
                embed.run(force_full=False)
                chunks_after = len(list(PROCESSED.glob("*.md")))

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "concepts": concept_names,
                    "chunks_added": max(0, chunks_after - chunks_before),
                }).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")

def _get_dashboard_html() -> str:
    return (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")


VIEW_SPEC_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vector DB → PPT — Interactive Template</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#0B1220;
    --panel:#101B2E;
    --panel-line:#243352;
    --panel-text:#B9C4D6;
    --paper:#F7F5EF;
    --paper-ink:#182230;
    --paper-muted:#78808C;
    --line-dashed:#CDC6B6;
    --accent-dark:#F0B429;
    --accent-light:#A8630E;
    --font-display:'Fraunces', serif;
    --font-body:'IBM Plex Sans', sans-serif;
    --font-mono:'IBM Plex Mono', monospace;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--ink);color:var(--panel-text);font-family:var(--font-body);overflow:hidden;}
  .app{height:100vh;display:flex;flex-direction:column;}

  /* ---------- Top bar ---------- */
  .topbar{
    display:flex;align-items:center;justify-content:space-between;
    padding:12px 24px;border-bottom:1px solid var(--panel-line);
    background:
      repeating-linear-gradient(0deg, rgba(255,255,255,0.02) 0 1px, transparent 1px 24px),
      repeating-linear-gradient(90deg, rgba(255,255,255,0.02) 0 1px, transparent 1px 24px),
      var(--panel);
    flex-shrink:0;
  }
  .brand{display:flex;flex-direction:column;gap:2px;}
  .brand .eyebrow{font-family:var(--font-mono);font-size:10px;letter-spacing:.12em;color:var(--accent-dark);text-transform:uppercase;}
  .brand .name{font-family:var(--font-display);font-size:18px;font-weight:600;color:#EDEFF3;}

  .topbar-controls{display:flex;align-items:center;gap:12px;}
  .mode-toggle{display:flex;border:1px solid var(--panel-line);border-radius:999px;overflow:hidden;}
  .mode-toggle button{
    padding:7px 16px;font-family:var(--font-mono);font-size:11px;letter-spacing:.05em;
    text-transform:uppercase;background:transparent;color:#9AA6BC;border:none;cursor:pointer;
  }
  .mode-toggle button.active{background:var(--accent-dark);color:var(--ink);font-weight:600;}
  .icon-btn{
    display:flex;align-items:center;gap:6px;padding:7px 14px;border-radius:999px;
    border:1px solid var(--panel-line);background:transparent;color:#9AA6BC;
    font-family:var(--font-mono);font-size:11px;letter-spacing:.05em;text-transform:uppercase;cursor:pointer;
  }
  .icon-btn:hover{border-color:var(--accent-dark);color:var(--accent-dark);}
  button:focus-visible, .rail-item:focus-visible{outline:2px solid var(--accent-dark);outline-offset:2px;}

  /* ---------- Workspace ---------- */
  .workspace{flex:1;display:flex;min-height:0;}
  .rail{
    width:190px;flex-shrink:0;border-right:1px solid var(--panel-line);
    padding:18px 14px;display:flex;flex-direction:column;gap:12px;overflow-y:auto;
    background:var(--panel);
  }
  .rail-item{
    all:unset;cursor:pointer;border:1px solid var(--panel-line);border-radius:6px;
    padding:10px;display:flex;flex-direction:column;gap:8px;
    animation:railIn .4s ease both;
  }
  .rail-item:hover{border-color:var(--accent-dark);}
  .rail-item.active{border-color:var(--accent-dark);background:rgba(240,180,41,0.06);}
  .rail-mini{
    height:52px;border-radius:3px;background:var(--paper);display:flex;gap:4px;padding:6px;overflow:hidden;
  }
  .rail-mini .mblock{background:var(--line-dashed);border-radius:2px;}
  .rail-meta{display:flex;align-items:center;justify-content:space-between;}
  .rail-num{font-family:var(--font-mono);font-size:10px;color:var(--accent-dark);}
  .rail-label{font-family:var(--font-body);font-size:11px;color:#C7D0DF;text-overflow:ellipsis;overflow:hidden;white-space:nowrap;}

  .stage{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:16px;min-width:0;}
  .artboard-shell{width:100%;max-width:920px;}
  .artboard{
    width:100%;aspect-ratio:16/9;background:var(--paper);color:var(--paper-ink);
    border-radius:6px;box-shadow:0 20px 60px rgba(0,0,0,0.45);
    padding:40px 48px;position:relative;overflow:hidden;
  }

  .controls{display:flex;align-items:center;justify-content:center;gap:18px;margin-top:16px;}
  .nav-btn{
    all:unset;cursor:pointer;width:34px;height:34px;border-radius:50%;
    border:1px solid var(--panel-line);display:flex;align-items:center;justify-content:center;color:#C7D0DF;
  }
  .nav-btn:hover{border-color:var(--accent-dark);color:var(--accent-dark);}
  .counter{font-family:var(--font-mono);font-size:12px;letter-spacing:.08em;color:#9AA6BC;}

  .footnote{
    text-align:center;padding:10px 24px 14px;font-family:var(--font-mono);font-size:10.5px;
    color:#5B6478;letter-spacing:.02em;flex-shrink:0;
  }
  .footnote b{color:#8892A6;font-weight:500;}

  /* ---------- Field / value system ---------- */
  .field{margin-bottom:14px;}
  .field-label{
    display:block;font-family:var(--font-mono);font-size:10px;letter-spacing:.06em;
    color:var(--accent-light);text-transform:uppercase;margin-bottom:6px;
  }
  .field-value{font-family:var(--font-body);color:var(--paper-ink);animation:fieldIn .35s ease both;}
  .skel{height:10px;border-bottom:1.5px dashed var(--line-dashed);margin-bottom:8px;}
  .skel-block{border:1.5px dashed var(--line-dashed);border-radius:4px;}

  .deck-title-value{font-family:var(--font-display);font-weight:600;font-size:38px;line-height:1.1;}
  .slide-title-value{font-family:var(--font-display);font-weight:600;font-size:24px;line-height:1.2;}
  .caption-value{font-family:var(--font-mono);font-size:11px;color:var(--paper-muted);}

  .bullets{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px;}
  .bullets li{position:relative;padding-left:16px;font-size:14px;line-height:1.4;animation:fieldIn .35s ease both;color:var(--paper-ink);}
  .bullets li::before{content:'';position:absolute;left:0;top:7px;width:6px;height:6px;background:var(--accent-light);}

  @keyframes fieldIn{from{opacity:0;transform:translateY(4px);}to{opacity:1;transform:translateY(0);}}
  @keyframes railIn{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}

  /* ---------- Image slot ---------- */
  .img-slot{position:relative;border:1.5px dashed var(--line-dashed);border-radius:3px;background:rgba(0,0,0,0.015);overflow:hidden;animation:fieldIn .35s ease both;}
  .crop{position:absolute;width:13px;height:13px;pointer-events:none;}
  .crop.tl{top:-1px;left:-1px;border-top:2px solid var(--accent-light);border-left:2px solid var(--accent-light);}
  .crop.tr{top:-1px;right:-1px;border-top:2px solid var(--accent-light);border-right:2px solid var(--accent-light);}
  .crop.bl{bottom:-1px;left:-1px;border-bottom:2px solid var(--accent-light);border-left:2px solid var(--accent-light);}
  .crop.br{bottom:-1px;right:-1px;border-bottom:2px solid var(--accent-light);border-right:2px solid var(--accent-light);}
  .slot-tag{
    position:absolute;top:6px;left:6px;font-family:var(--font-mono);font-size:9.5px;letter-spacing:.02em;
    background:var(--paper-ink);color:var(--paper);padding:2px 6px;border-radius:2px;z-index:2;
  }
  .slot-body{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}
  .slot-empty{display:flex;flex-direction:column;align-items:center;gap:6px;color:var(--paper-muted);padding:20px;text-align:center;}
  .slot-icon{width:24px;height:24px;opacity:.55;}
  .slot-spec{font-family:var(--font-mono);font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;text-align:center;}
  .slot-body img{max-width:100%;max-height:100%;object-fit:contain;}

  /* ---------- Slide layout helpers ---------- */
  .layout-title{height:100%;display:flex;flex-direction:column;justify-content:center;gap:18px;position:relative;}
  .title-eyebrow{font-family:var(--font-mono);font-size:11px;letter-spacing:.12em;color:var(--accent-light);text-transform:uppercase;}
  .title-watermark{position:absolute;right:-20px;bottom:-30px;opacity:.06;width:280px;}

  .layout-concept{height:100%;display:grid;grid-template-columns:0.9fr 1.1fr;gap:32px;align-items:center;}
  .layout-diagram{height:100%;display:flex;flex-direction:column;gap:14px;}
  .layout-diagram .img-slot{flex:1;min-height:0;}
  .diagram-explain{display:flex;gap:22px;}
  .diagram-explain .bullets{flex-direction:row;flex-wrap:wrap;}
  .diagram-explain .bullets li{flex:1;min-width:150px;}

  .layout-summary{height:100%;display:flex;flex-direction:column;justify-content:center;gap:26px;}

  /* ---------- Schema panel ---------- */
  .schema-panel{
    position:fixed;top:0;right:-440px;width:420px;max-width:92vw;height:100%;
    background:var(--panel);border-left:1px solid var(--panel-line);
    transition:right .3s ease;z-index:60;padding:22px;display:flex;flex-direction:column;
  }
  .schema-panel.open{right:0;}
  .schema-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-shrink:0;}
  .schema-head h3{font-family:var(--font-display);font-size:16px;margin:0;color:#EDEFF3;}
  .schema-close{all:unset;cursor:pointer;color:#9AA6BC;font-family:var(--font-mono);font-size:11px;}
  .scrim{position:fixed;inset:0;background:rgba(0,0,0,0.4);opacity:0;pointer-events:none;transition:opacity .3s ease;z-index:55;}
  .scrim.open{opacity:1;pointer-events:auto;}

  @media (max-width:860px){
    .workspace{flex-direction:column;}
    .rail{width:100%;flex-direction:row;overflow-x:auto;border-right:none;border-bottom:1px solid var(--panel-line);}
    .rail-item{min-width:130px;}
    .layout-concept{grid-template-columns:1fr;}
    .artboard{padding:26px 22px;}
    .deck-title-value{font-size:28px;}
  }
  @media (prefers-reduced-motion: reduce){
    *{animation:none !important;transition:none !important;}
  }
</style>
</head>
<body>
<div class="app">

  <div class="topbar">
    <div class="brand">
      <span class="eyebrow" id="deckSubject">IIT Mandi · LMS Pilot</span>
      <span class="name" id="deckTitle">Loading presentation...</span>
    </div>
    <div class="topbar-controls">
      <div class="mode-toggle" role="tablist" aria-label="Template mode">
        <button id="modeBlueprint" role="tab" aria-selected="false">Blueprint</button>
        <button id="modeFilled" class="active" role="tab" aria-selected="true">Filled</button>
      </div>
      <button class="icon-btn" id="schemaBtn" aria-haspopup="true" aria-controls="schemaPanel">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"/></svg>
        Tweak Spec
      </button>
    </div>
  </div>

  <div class="workspace">
    <nav class="rail" id="rail" aria-label="Slide thumbnails"></nav>

    <main class="stage">
      <div class="artboard-shell">
        <div class="artboard" id="artboard" role="region" aria-label="Slide preview"></div>
        <div class="controls">
          <button class="nav-btn" id="prevBtn" aria-label="Previous slide">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>
          </button>
          <span class="counter" id="counter">00 / 00</span>
          <button class="nav-btn" id="nextBtn" aria-label="Next slide">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
          </button>
        </div>
      </div>
    </main>
  </div>

  <div class="footnote">Field keys mirror <b>slide_spec.schema.json</b> — live Beamer PDF rebuild on Save.</div>
</div>

<div class="scrim" id="scrim"></div>
<aside class="schema-panel" id="schemaPanel" aria-label="Tweak Slide Deck JSON">
  <div class="schema-head">
    <h3>Tweak Slide Spec JSON</h3>
    <button class="schema-close" id="schemaClose">Close ✕</button>
  </div>
  <textarea id="jsonEditor" style="width:100%; flex:1; background:#0C1526; border:1px solid var(--panel-line); border-radius:6px; padding:14px; font-family:var(--font-mono); font-size:11px; line-height:1.5; color:#CFE3D8; resize:none; outline:none; margin-bottom:12px;"></textarea>
  <button id="saveBtn" class="icon-btn" style="width:100%; justify-content:center; background:var(--accent-dark); color:var(--ink); font-weight:600; padding:12px; border:none; cursor:pointer;">Save & Re-render PDF</button>
</aside>

<script>
const svgNeuralNet = `
<svg viewBox="0 0 200 140" xmlns="http://www.w3.org/2000/svg">
  <g stroke="#B8C4D6" stroke-width="1">
    <line x1="30" y1="30" x2="100" y2="20"/><line x1="30" y1="30" x2="100" y2="70"/><line x1="30" y1="30" x2="100" y2="120"/>
    <line x1="30" y1="70" x2="100" y2="20"/><line x1="30" y1="70" x2="100" y2="70"/><line x1="30" y1="70" x2="100" y2="120"/>
    <line x1="30" y1="110" x2="100" y2="20"/><line x1="30" y1="110" x2="100" y2="70"/><line x1="30" y1="110" x2="100" y2="120"/>
    <line x1="100" y1="20" x2="170" y2="70"/><line x1="100" y1="70" x2="170" y2="70"/><line x1="100" y1="120" x2="170" y2="70"/>
  </g>
  <g stroke="#A8630E" stroke-width="1.5" stroke-dasharray="4 3">
    <line x1="170" y1="70" x2="100" y2="20"/><line x1="100" y1="20" x2="30" y2="30"/>
  </g>
  <g fill="#182230"><circle cx="30" cy="30" r="7"/><circle cx="30" cy="70" r="7"/><circle cx="30" cy="110" r="7"/></g>
  <g fill="#182230"><circle cx="100" cy="20" r="7"/><circle cx="100" cy="70" r="7"/><circle cx="100" cy="120" r="7"/></g>
  <g fill="#A8630E"><circle cx="170" cy="70" r="8"/></g>
</svg>`;

const iconImage = `<svg class="slot-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/></svg>`;

const urlParams = new URLSearchParams(window.location.search);
const filename = urlParams.get('file');

let slides = [];
let current = 0;
let mode = 'filled';
let schemaOpen = false;
let currentSpec = {};

function esc(s){ return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function bulletsBlock(items, label){
  if(mode==='blueprint'){
    const widths=['92%','78%','85%','62%','70%'];
    return `<div class="field">
      <span class="field-label">{{${label}}}</span>
      <div>${(items || []).map((_,i)=>`<div class="skel" style="width:${widths[i%widths.length]}"></div>`).join('')}</div>
    </div>`;
  }
  return `<div class="field">
    <span class="field-label">{{${label}}}</span>
    <ul class="bullets">${(items || []).map(t=>`<li>${esc(t)}</li>`).join('')}</ul>
  </div>`;
}

function renderTitle(s) {
  const title = s.title || "Untitled Presentation";
  const subtitle = s.subtitle || "";
  const query = s.query || "";
  return `
    <div class="layout-title">
      <div class="title-watermark">${svgNeuralNet}</div>
      <span class="title-eyebrow">${mode==='filled' ? esc(query) : '{{query}}'}</span>
      <div class="field">
        <span class="field-label">{{title}}</span>
        ${mode==='filled' ? `<div class="field-value deck-title-value">${esc(title)}</div>` : `<div class="skel-block" style="height:44px;width:72%"></div>`}
      </div>
      ${subtitle ? `
      <div class="field">
        <span class="field-label">{{subtitle}}</span>
        ${mode==='filled' ? `<div class="caption-value" style="font-size:16px;color:var(--paper-muted);">${esc(subtitle)}</div>` : `<div class="skel-block" style="height:20px;width:55%"></div>`}
      </div>` : ''}
    </div>
  `;
}

function renderSection(s) {
  const number = s.number || 1;
  const title = s.title || "";
  return `
    <div class="layout-title" style="align-items: center; justify-content: center; text-align: center; height:100%;">
      <span class="title-eyebrow">SECTION ${number}</span>
      <div class="field" style="width:100%;">
        <span class="field-label">{{title}}</span>
        ${mode==='filled' ? `<div class="field-value deck-title-value">${esc(title)}</div>` : `<div class="skel-block" style="height:44px;width:60%;margin:0 auto;"></div>`}
      </div>
    </div>
  `;
}

function renderBullets(s) {
  const title = s.title || "";
  const bullets = s.bullets || [];
  const src = s.source ? \`Source: \${s.source.doc || ""} \${s.source.loc || ""}\`.trim() : "";
  return `
    <div class="layout-summary">
      <div class="field">
        <span class="field-label">{{title}}</span>
        ${mode==='filled' ? `<div class="field-value slide-title-value">${esc(title)}</div>` : `<div class="skel-block" style="height:30px;width:55%"></div>`}
      </div>
      ${bulletsBlock(bullets, 'bullets')}
      ${s.source && mode==='filled' ? `<div class="field" style="margin-top:auto;padding-top:10px;"><span class="field-label">SOURCE</span><div class="caption-value">${esc(src)}</div></div>` : ''}
    </div>
  `;
}

function renderImageSlide(s) {
  const title = s.title || "";
  const imgPath = s.image || "";
  const caption = s.caption || "";
  const src = s.source ? \`Source: \${s.source.doc || ""} \${s.source.loc || ""}\`.trim() : "";
  
  const cleanPath = imgPath.replace(/^\/?/, '/');
  
  const imgHtml = mode === 'filled' && imgPath
    ? `<div class="slot-body"><img src="\${cleanPath}" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;" onerror="this.outerHTML='<div class=slot-empty>${iconImage}<span>Image not found: \${esc(imgPath)}</span></div>'" /></div>`
    : `<div class="slot-body"><div class="slot-empty">${iconImage}<span class="slot-spec">\${esc(imgPath || 'IMG_PATH')}</span></div></div>`;

  return `
    <div class="layout-concept">
      <div class="img-slot" style="height:100%; min-height:220px; display:flex; align-items:center; justify-content:center;">
        <span class="crop tl"></span><span class="crop tr"></span><span class="crop bl"></span><span class="crop br"></span>
        <span class="slot-tag">{{image}}</span>
        \${imgHtml}
      </div>
      <div>
        <div class="field">
          <span class="field-label">{{title}}</span>
          ${mode==='filled' ? `<div class="field-value slide-title-value">${esc(title)}</div>` : `<div class="skel-block" style="height:30px;width:80%"></div>`}
        </div>
        <div class="field">
          <span class="field-label">{{caption}}</span>
          ${mode==='filled' ? `<div class="caption-value" style="font-size:13px;line-height:1.4;color:var(--paper-ink);">${esc(caption)}</div>` : `<div class="skel" style="width:90%"></div><div class="skel" style="width:75%"></div>`}
        </div>
        ${s.source && mode==='filled' ? `<div class="field" style="margin-top:16px;"><span class="field-label">SOURCE</span><div class="caption-value">${esc(src)}</div></div>` : ''}
      </div>
    </div>
  `;
}

function renderDiagramSlide(s) {
  const title = s.title || "";
  const imgPath = s.image || "";
  const explains = s.explains || [];
  const src = s.source ? \`Source: \${s.source.doc || ""} \${s.source.loc || ""}\`.trim() : "";
  
  const cleanPath = imgPath.replace(/^\/?/, '/');
  const imgHtml = mode === 'filled' && imgPath
    ? `<div class="slot-body"><img src="\${cleanPath}" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;" onerror="this.outerHTML='<div class=slot-empty>${iconImage}<span>Diagram not found: \${esc(imgPath)}</span></div>'" /></div>`
    : `<div class="slot-body"><div class="slot-empty">${iconImage}<span class="slot-spec">\${esc(imgPath || 'DIAGRAM_PATH')}</span></div></div>`;

  return `
    <div class="layout-diagram">
      <div class="field">
        <span class="field-label">{{title}}</span>
        ${mode==='filled' ? `<div class="field-value slide-title-value">${esc(title)}</div>` : `<div class="skel-block" style="height:30px;width:55%"></div>`}
      </div>
      <div class="compare-grid" style="grid-template-columns: 1.1fr 0.9fr; gap: 20px; flex: 1; min-height: 0;">
        <div class="img-slot" style="height:100%; display:flex; align-items:center; justify-content:center;">
          <span class="crop tl"></span><span class="crop tr"></span><span class="crop bl"></span><span class="crop br"></span>
          <span class="slot-tag">{{image}}</span>
          \${imgHtml}
        </div>
        <div style="display:flex; flex-direction:column; justify-content:space-between; min-height:0;">
          \${bulletsBlock(explains, 'explains')}
          ${s.source && mode==='filled' ? `<div class="field" style="margin-top:10px;"><span class="field-label">SOURCE</span><div class="caption-value" style="font-size:10px;">${esc(src)}</div></div>` : ''}
        </div>
      </div>
    </div>
  `;
}

function renderQuote(s) {
  const text = s.text || "";
  const src = s.source || "";
  return `
    <div class="layout-title" style="align-items: center; justify-content: center; text-align: center; height:100%;">
      <span class="title-eyebrow">QUOTE</span>
      <div class="field" style="width:100%;">
        <span class="field-label">{{text}}</span>
        ${mode==='filled' ? `<div class="field-value deck-title-value" style="font-size:26px; font-style:italic; font-family:var(--font-display); line-height:1.4;">“\${esc(text)}”</div>` : `<div class="skel-block" style="height:36px;width:80%;margin:0 auto;margin-bottom:8px;"></div><div class="skel-block" style="height:36px;width:60%;margin:0 auto;"></div>`}
      </div>
      \${src ? `
      <div class="field">
        <span class="field-label">{{source}}</span>
        ${mode==='filled' ? `<div class="caption-value" style="font-size:14px; font-weight:500;">— \${esc(src)}</div>` : `<div class="skel" style="width:25%;margin:0 auto;"></div>`}
      </div>` : ''}
    </div>
  `;
}

function renderSummary(s) {
  const title = s.title || "Key Takeaways";
  const takeaways = s.takeaways || [];
  return `
    <div class="layout-summary">
      <div class="field">
        <span class="field-label">{{title}}</span>
        ${mode==='filled' ? `<div class="field-value slide-title-value">${esc(title)}</div>` : `<div class="skel-block" style="height:30px;width:55%"></div>`}
      </div>
      \${bulletsBlock(takeaways, 'takeaways')}
    </div>
  `;
}

function buildSlideHTML(s){
  switch(s.type){
    case 'title': return renderTitle(s);
    case 'section': return renderSection(s);
    case 'bullets': return renderBullets(s);
    case 'image': return renderImageSlide(s);
    case 'diagram': return renderDiagramSlide(s);
    case 'quote': return renderQuote(s);
    case 'summary': return renderSummary(s);
    case 'tikz': return renderImageSlide(s);
    case 'matplotlib': return renderImageSlide(s);
    default: return `<div style="padding:20px;">Unknown Slide Type: \${esc(s.type)}</div>`;
  }
}

function miniLayout(type){
  const block = (w,h,extra)=>\`<div class="mblock" style="width:\${w};height:\${h};\${extra||''}"></div>\`;
  switch(type){
    case 'title': return \`\${block('70%','8px')}\${block('45%','5px')}\`;
    case 'section': return \`<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;width:100%;align-items:center;">\${block('40%','6px')}\${block('70%','8px')}</div>\`;
    case 'bullets': return \`<div style="display:flex;flex-direction:column;gap:4px;width:100%;justify-content:center;">\${block('50%','5px')}\${block('90%','4px')}\${block('80%','4px')}</div>\`;
    case 'image': return \`\${block('38%','100%')}<div style="display:flex;flex-direction:column;gap:4px;flex:1;justify-content:center;">\${block('90%','5px')}\${block('70%','5px')}</div>\`;
    case 'diagram': return \`<div style="display:flex;flex-direction:column;gap:4px;width:100%;">\${block('50%','5px')}\${block('100%','30px')}</div>\`;
    case 'quote': return \`<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;width:100%;align-items:center;">\${block('80%','5px')}\${block('30%','4px')}</div>\`;
    case 'summary': return \`<div style="display:flex;flex-direction:column;gap:5px;width:100%;">\${block('80%','5px')}\${block('65%','5px')}\${block('72%','5px')}</div>\`;
    case 'tikz': return miniLayout('image');
    case 'matplotlib': return miniLayout('image');
    default: return block('100%','100%');
  }
}

function renderRail(){
  const rail = document.getElementById('rail');
  rail.innerHTML = slides.map((s,i)=>`
    <button class="rail-item \${i===current?'active':''}" data-i="\${i}" style="animation-delay:\${i*0.05}s">
      <div class="rail-mini">\${miniLayout(s.type)}</div>
      <div class="rail-meta"><span class="rail-num">\${String(i).padStart(2,'0')}</span><span class="rail-label" style="text-transform:capitalize;">\${s.type}</span></div>
    </button>
  `).join('');
  rail.querySelectorAll('.rail-item').forEach(btn=>{
    btn.addEventListener('click', ()=>{ current = parseInt(btn.dataset.i); renderAll(); });
  });
}

function renderAll(){
  if (!slides || slides.length === 0) return;
  document.getElementById('artboard').innerHTML = buildSlideHTML(slides[current]);
  document.getElementById('counter').textContent = \`\${String(current).padStart(2,'0')} / \${String(slides.length-1).padStart(2,'0')}\`;
  document.querySelectorAll('.rail-item').forEach((el,i)=> el.classList.toggle('active', i===current));
}

/* ---------- Events ---------- */
document.getElementById('prevBtn').addEventListener('click', ()=>{ current = (current-1+slides.length)%slides.length; renderAll(); });
document.getElementById('nextBtn').addEventListener('click', ()=>{ current = (current+1)%slides.length; renderAll(); });
document.addEventListener('keydown', (e)=>{
  if(e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  if(e.key==='ArrowLeft'){ current=(current-1+slides.length)%slides.length; renderAll(); }
  if(e.key==='ArrowRight'){ current=(current+1)%slides.length; renderAll(); }
  if(e.key==='Escape' && schemaOpen){ toggleSchema(false); }
});

document.getElementById('modeBlueprint').addEventListener('click', ()=>{ setMode('blueprint'); });
document.getElementById('modeFilled').addEventListener('click', ()=>{ setMode('filled'); });
function setMode(m){
  mode = m;
  document.getElementById('modeBlueprint').classList.toggle('active', m==='blueprint');
  document.getElementById('modeFilled').classList.toggle('active', m==='filled');
  document.getElementById('modeBlueprint').setAttribute('aria-selected', m==='blueprint');
  document.getElementById('modeFilled').setAttribute('aria-selected', m==='filled');
  renderAll();
}

function toggleSchema(force){
  schemaOpen = force!==undefined ? force : !schemaOpen;
  document.getElementById('schemaPanel').classList.toggle('open', schemaOpen);
  document.getElementById('scrim').classList.toggle('open', schemaOpen);
}
document.getElementById('schemaBtn').addEventListener('click', ()=>toggleSchema());
document.getElementById('schemaClose').addEventListener('click', ()=>toggleSchema(false));
document.getElementById('scrim').addEventListener('click', ()=>toggleSchema(false));

/* ---------- API Save Trigger ---------- */
document.getElementById('saveBtn').addEventListener('click', async () => {
  const saveBtn = document.getElementById('saveBtn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving & compiling...';
  
  try {
    const updatedSpec = JSON.parse(document.getElementById('jsonEditor').value);
    const res = await fetch('/api/save_spec', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        file: filename,
        spec: updatedSpec
      })
    });
    
    if (res.ok) {
      saveBtn.textContent = 'Saved successfully!';
      currentSpec = updatedSpec;
      initSlides(currentSpec);
      if (window.parent && typeof window.parent.fetchStatus === 'function') {
        window.parent.fetchStatus();
      }
    } else {
      const errText = await res.text();
      alert('Error saving: ' + errText);
    }
  } catch (err) {
    alert('Invalid JSON format: ' + err.message);
  } finally {
    saveBtn.disabled = false;
    setTimeout(() => {
      saveBtn.textContent = 'Save & Re-render PDF';
    }, 2000);
  }
});

/* ---------- Data Loading ---------- */
function initSlides(spec) {
  document.getElementById('deckTitle').textContent = spec.title || "Slide Spec";
  document.getElementById('deckSubject').textContent = \`IIT Mandi · \${spec.query || "LMS Generation"}\`;
  
  slides = [
    { type: 'title', title: spec.title, subtitle: spec.subtitle, query: spec.query }
  ];
  if (spec.slides && Array.isArray(spec.slides)) {
    slides = slides.concat(spec.slides);
  }
  
  document.getElementById('jsonEditor').value = JSON.stringify(spec, null, 2);
  renderRail();
  renderAll();
}

async function loadSpec() {
  if (!filename) {
    document.getElementById('artboard').innerHTML = '<div style="padding:20px;color:var(--paper-ink);">No spec file specified in URL query.</div>';
    return;
  }
  try {
    const res = await fetch(\`/output/\${filename}\`);
    if (!res.ok) throw new Error("Could not load slide spec");
    currentSpec = await res.json();
    initSlides(currentSpec);
  } catch (err) {
    document.getElementById('artboard').innerHTML = \`<div style="padding:20px;color:var(--paper-ink);">Error loading spec: \${esc(err.message)}</div>\`;
  }
}

async function sendChat() {
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  const box = document.getElementById('chatMessages');
  box.innerHTML += `<div style="margin-bottom:4px"><b style="color:var(--accent)">You:</b> ${esc(msg)}</div>`;
  box.scrollTop = box.scrollHeight;
  document.getElementById('chatSubject').textContent = 'Processing...';
  try {
    await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})});
    // Poll for result
    const poll = setInterval(async () => {
      const r = await fetch('/api/chat_result');
      const d = await r.json();
      if (d.status === 'success' && d.result) {
        clearInterval(poll);
        const res = d.result;
        const subject = res.subject_detected || '';
        document.getElementById('chatSubject').textContent = subject ? `Subject detected: ${subject}` : '';
        const files = (res.files || []).map(f => {
          const name = f.split('/').pop();
          return `<a href="/output/${name}" target="_blank" style="color:var(--accent)">${name}</a>`;
        }).join(' · ');
        box.innerHTML += `<div style="margin-bottom:4px"><b style="color:var(--text-dim)">AI:</b> Generated ${res.type || ''}. ${files}</div>`;
        box.scrollTop = box.scrollHeight;
        loadStatus();
      } else if (d.status === 'failed') {
        clearInterval(poll);
        document.getElementById('chatSubject').textContent = '';
        box.innerHTML += `<div style="margin-bottom:4px;color:#f87171"><b>Error:</b> ${esc((d.result && d.result.message) || 'failed')}</div>`;
        box.scrollTop = box.scrollHeight;
      }
    }, 2000);
  } catch(e) {
    box.innerHTML += `<div style="color:#f87171">Network error: ${esc(e.message)}</div>`;
    document.getElementById('chatSubject').textContent = '';
  }
}

/* ---------- Init ---------- */
loadSpec();
</script>
</body>
</html>"""

def main() -> None:
    port = 8000
    server_address = ("", port)
    
    # Try different ports if 8000 is occupied
    while True:
        try:
            httpd = http.server.HTTPServer(server_address, PipelineHandler)
            break
        except OSError:
            print(f"[Server] Port {port} is in use. Trying {port + 1}...")
            port += 1
            server_address = ("", port)

    print(f"\n=======================================================")
    print(f"  LMS Pilot Control Server running at:")
    print(f"  http://localhost:{port}")
    print(f"=======================================================\n")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Stopping server...")
        httpd.server_close()

if __name__ == "__main__":
    main()
