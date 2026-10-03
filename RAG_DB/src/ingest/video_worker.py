import os
import json
import uuid
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any
import requests

from core.models import SourceUnit, Locator
from ingest import jobs
from ingest.units import write_units
from core.db import get_connection

logger = logging.getLogger(__name__)

# Config: Whisper server endpoint & untracked media directory
WHISPER_URL = os.getenv("WHISPER_SERVER_URL", "http://localhost:8000/transcribe")
MEDIA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "processed" / "media"

def extract_audio(video_path: Path, output_wav: Path) -> None:
    """Extracts 16kHz mono audio from video using FFmpeg for processing."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(output_wav)]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg extraction failed: {e.stderr.decode()}")
        raise RuntimeError(f"FFmpeg failed: {e.stderr.decode()}")
    except FileNotFoundError:
        # Fallback error if FFmpeg isn't installed in the container/system
        logger.error("FFmpeg executable not found. Ensure ffmpeg is installed.")
        raise

def get_transcript(audio_path: Path) -> List[Dict[str, Any]]:
    """Sends audio to Whisper Server (with VAD enabled) and retrieves timestamps."""
    try:
        with open(audio_path, "rb") as f:
            # TRD VAD requirement: Faster-Whisper uses Silero VAD natively via vad_filter flag
            response = requests.post(
                WHISPER_URL, 
                files={"file": f}, 
                data={"vad_filter": "true", "word_timestamps": "true"},
                timeout=300
            )
        response.raise_for_status()
        
        data = response.json()
        return data.get("segments", [])
    except Exception as e:
        logger.error(f"Whisper transcription failed: {e}")
        raise

def chunk_transcript(source_id: str, title: str, segments: List[Dict[str, Any]], target_duration: int = 30) -> List[SourceUnit]:
    """
    Chunks transcript segments into SourceUnits (approx target_duration seconds).
    Aligns on segment boundaries to maintain semantic cohesion.
    """
    units = []
    current_chunk_text = []
    current_start = None
    current_end = None
    
    for seg in segments:
        seg_start = seg.get("start", 0.0)
        seg_end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()
        
        if current_start is None:
            current_start = seg_start
        
        current_chunk_text.append(text)
        current_end = seg_end
        
        # Once chunk reaches target duration, finalize the SourceUnit
        if (current_end - current_start) >= target_duration:
            unit_id = f"u_{uuid.uuid4().hex[:8]}"
            units.append(
                SourceUnit(
                    unit_id=unit_id,
                    source_id=source_id,
                    source_type="video",
                    kind="transcript",
                    title=title,
                    text=" ".join(current_chunk_text),
                    locator=Locator(t_start=current_start, t_end=current_end)
                )
            )
            current_chunk_text = []
            current_start = None
            
    # Flush any remaining text as the final chunk
    if current_chunk_text and current_start is not None:
        unit_id = f"u_{uuid.uuid4().hex[:8]}"
        units.append(
            SourceUnit(
                unit_id=unit_id,
                source_id=source_id,
                source_type="video",
                kind="transcript",
                title=title,
                text=" ".join(current_chunk_text),
                locator=Locator(t_start=current_start, t_end=current_end)
            )
        )
        
    return units

def process_video(job_id: str, source_id: str, video_path: Path) -> None:
    """Main Orchestrator for Task B1: Video Ingestion"""
    temp_audio = MEDIA_DIR / "temp" / f"{job_id}_{source_id}.wav"
    
    try:
        # 1. Fetch Source Metadata
        conn = get_connection()
        row = conn.execute("SELECT title FROM sources WHERE source_id=?", (source_id,)).fetchone()
        conn.close()
        
        if not row:
            raise ValueError(f"Source ID {source_id} not found in database.")
        title = row[0]
        
        # 2. Extract Audio (FFmpeg)
        jobs.update_job_progress(job_id, stage='extracting_audio', progress=0.1)
        extract_audio(video_path, temp_audio)
        
        # 3. Transcribe & VAD (Whisper Server)
        jobs.update_job_progress(job_id, stage='transcribing', progress=0.4)
        segments = get_transcript(temp_audio)
        
        # 4. Chunking (SourceUnit mapping)
        jobs.update_job_progress(job_id, stage='chunking', progress=0.8)
        units = chunk_transcript(source_id, title, segments, target_duration=30)
        
        # 5. Persist Units (SQLite + Qdrant sync via units.write_units)
        write_units(units)
        
        # 6. Mark Job & Source Complete
        jobs.update_job_progress(job_id, stage='complete', progress=1.0, units_created=len(units))
        
        conn = get_connection()
        conn.execute("UPDATE sources SET status='done' WHERE source_id=?", (source_id,))
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Video processing job {job_id} failed: {e}")
        jobs.fail_job(job_id, str(e))
    finally:
        # Strict directory hygiene: destroy temporary artifacts
        if temp_audio.exists():
            temp_audio.unlink()
