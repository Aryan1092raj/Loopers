import sqlite3
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def get_db_path() -> Path:
    """Returns the absolute path to course.db, falling back to RAG_DB/metadata/course.db"""
    # Assuming this is run from within the RAG_DB folder structure or loopers root
    default_metadata_dir = Path(__file__).resolve().parent.parent.parent / "metadata"
    db_path = Path(os.getenv("COURSE_DB", str(default_metadata_dir / "course.db"))).resolve()
    return db_path

def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_db_path()
    
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db(db_path: Path | None = None):
    conn = get_connection(db_path)
    cursor = conn.cursor()

    ddl = """
    CREATE TABLE IF NOT EXISTS sources (
      source_id   TEXT PRIMARY KEY,
      kind        TEXT NOT NULL CHECK (kind IN ('video','slides','textbook')),
      title       TEXT NOT NULL,
      file_name   TEXT NOT NULL,
      media_path  TEXT,
      lecture_no  INTEGER,
      lang        TEXT NOT NULL DEFAULT 'en',
      status      TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
      created_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS jobs (
      job_id TEXT PRIMARY KEY, 
      source_id TEXT REFERENCES sources(source_id),
      stage TEXT, 
      progress REAL DEFAULT 0, 
      units_created INTEGER DEFAULT 0,
      error TEXT, 
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS units (
      unit_id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES sources(source_id),
      kind TEXT NOT NULL CHECK (kind IN ('text','figure','transcript')),
      heading TEXT, 
      text TEXT NOT NULL,
      page_no INTEGER, 
      slide_no INTEGER, 
      t_start REAL, 
      t_end REAL,
      matched_slide_page INTEGER, 
      bbox_json TEXT, 
      image_path TEXT, 
      latex_json TEXT,
      lang TEXT, 
      topic_id TEXT, 
      has_math INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_units_source ON units(source_id);

    CREATE TABLE IF NOT EXISTS concepts (
      concept_id TEXT PRIMARY KEY, 
      name TEXT NOT NULL,
      level TEXT NOT NULL CHECK (level IN ('topic','subtopic','concept')),
      parent_id TEXT REFERENCES concepts(concept_id),
      aliases_json TEXT NOT NULL DEFAULT '[]', 
      description TEXT,
      first_seen_lecture INTEGER
    );
    
    CREATE TABLE IF NOT EXISTS concept_edges (
      src TEXT NOT NULL REFERENCES concepts(concept_id), 
      dst TEXT NOT NULL REFERENCES concepts(concept_id),
      weight REAL NOT NULL, 
      evidence TEXT,
      PRIMARY KEY (src, dst)
    );
    
    CREATE TABLE IF NOT EXISTS unit_concepts (
      unit_id TEXT NOT NULL REFERENCES units(unit_id), 
      concept_id TEXT NOT NULL REFERENCES concepts(concept_id),
      score REAL NOT NULL, 
      PRIMARY KEY (unit_id, concept_id)
    );

    CREATE TABLE IF NOT EXISTS students (
      student_id TEXT PRIMARY KEY, 
      name TEXT, 
      lang TEXT DEFAULT 'en',
      exam_date TEXT, 
      intake_json TEXT, 
      created_at TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS mastery (
      student_id TEXT NOT NULL REFERENCES students(student_id), 
      concept_id TEXT NOT NULL REFERENCES concepts(concept_id),
      p_know REAL NOT NULL, 
      stability REAL, 
      last_review TEXT, 
      n_obs INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (student_id, concept_id)
    );
    
    CREATE TABLE IF NOT EXISTS mastery_events (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT NOT NULL, 
      concept_id TEXT NOT NULL, 
      ts TEXT NOT NULL,
      source TEXT NOT NULL CHECK (source IN ('quiz','chat','diagnostic','intake','propagation')),
      ref_id TEXT, 
      y REAL, 
      p_before REAL NOT NULL, 
      p_after REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS items (
      item_id TEXT PRIMARY KEY, 
      concept_id TEXT NOT NULL, 
      topic_id TEXT NOT NULL,
      type TEXT NOT NULL CHECK (type IN ('mcq','short','numeric')), 
      bloom TEXT NOT NULL,
      stem TEXT NOT NULL, 
      options_json TEXT, 
      answer_json TEXT NOT NULL, 
      rubric_json TEXT,
      unit_ids_json TEXT NOT NULL, 
      difficulty_b REAL NOT NULL, 
      discrimination_a REAL NOT NULL DEFAULT 1.0,
      embedding BLOB, 
      verified INTEGER NOT NULL DEFAULT 0, 
      verifier_json TEXT, 
      created_at TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS quizzes (
      quiz_id TEXT PRIMARY KEY, 
      student_id TEXT NOT NULL,
      mode TEXT NOT NULL CHECK (mode IN ('diagnostic','practice','mock')),
      scope_json TEXT NOT NULL, 
      created_at TEXT NOT NULL, 
      submitted_at TEXT
    );
    
    CREATE TABLE IF NOT EXISTS quiz_items (
      quiz_id TEXT NOT NULL, 
      item_id TEXT NOT NULL, 
      position INTEGER NOT NULL,
      PRIMARY KEY (quiz_id, item_id)
    );
    
    CREATE TABLE IF NOT EXISTS attempts (
      attempt_id TEXT PRIMARY KEY, 
      quiz_id TEXT NOT NULL, 
      item_id TEXT NOT NULL, 
      student_id TEXT NOT NULL,
      response_json TEXT NOT NULL, 
      correct REAL NOT NULL,
      feedback_json TEXT, 
      misconception TEXT, 
      latency_ms INTEGER, 
      ts TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS served_items (
      student_id TEXT NOT NULL, 
      item_id TEXT NOT NULL, 
      ts TEXT NOT NULL,
      PRIMARY KEY (student_id, item_id)
    );

    CREATE TABLE IF NOT EXISTS chat_sessions (
      session_id TEXT PRIMARY KEY, 
      student_id TEXT, 
      created_at TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS chat_turns (
      turn_id INTEGER PRIMARY KEY AUTOINCREMENT, 
      session_id TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('user','assistant')), 
      content TEXT NOT NULL,
      mode TEXT, 
      citations_json TEXT, 
      gate_json TEXT, 
      ts TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS eval_runs (
      run_id TEXT PRIMARY KEY, 
      kind TEXT NOT NULL, 
      config_json TEXT, 
      metrics_json TEXT, 
      created_at TEXT NOT NULL
    );
    """

    try:
        cursor.executescript(ddl)
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

