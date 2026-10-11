import hashlib
import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any
import requests
import yt_dlp

from config import PROCESSED
from core.models import SourceUnit, Locator
from ingest import jobs
from ingest.units import write_units
from core.db import get_connection

logger = logging.getLogger(__name__)

WHISPER_URL = os.getenv("WHISPER_SERVER_URL", "http://localhost:8300/v1/audio/transcriptions")
MEDIA_DIR = PROCESSED / "media"
MAX_CLIP_SECONDS = 60  # longest slice of a transcript unit that is embedded as video + audio

def download_youtube_audio(url: str, output_path: Path) -> None:
    """Downloads best available audio from YouTube."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path),
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.error(f"yt-dlp download failed: {e}")
        raise RuntimeError(f"YouTube download failed: {e}")

def extract_audio(video_path: Path, output_wav: Path) -> None:
    """Extracts 16kHz mono audio from video using FFmpeg for VAD/Whisper."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(output_wav)]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg extraction failed: {e.stderr.decode()}")
        raise RuntimeError(f"FFmpeg failed: {e.stderr.decode()}")

def get_transcript(audio_path: Path) -> List[Dict[str, Any]]:
    """Sends audio to Whisper Server (with Silero VAD enabled) and retrieves timestamps."""
    try:
        with open(audio_path, "rb") as f:
            # Faster-Whisper uses Silero VAD natively via vad_filter flag
            response = requests.post(
                WHISPER_URL,
                files={"file": f},
                data={"response_format": "verbose_json", "timestamp_granularities[]": "segment"},
                # 16 kHz mono 16-bit wav: 32000 bytes per second. A CPU server can take about real time, so a
                # fixed 5-minute timeout failed every lecture longer than that.
                timeout=max(300, 2 * audio_path.stat().st_size / 32000)
            )
        response.raise_for_status()

        data = response.json()
        return [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"]} for s in data.get("segments", [])]
    except Exception as e:
        logger.warning(f"Whisper server failed ({e}); falling back to local faster-whisper")
        return _local_transcript(audio_path)


def _local_transcript(audio_path: Path) -> List[Dict[str, Any]]:
    # ponytail: CUDA runs large-v3-turbo fp16; CPU fallback is the multilingual base model in int8. Upgrade the CPU model if the server stays down.
    import torch
    from faster_whisper import WhisperModel

    if torch.cuda.is_available():
        try:  # CTranslate2 needs CUDA 12 cuBLAS, which a CUDA 13 torch image does not ship
            segments, _ = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16").transcribe(str(audio_path), vad_filter=True)
            return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
        except (RuntimeError, OSError) as e:
            logger.warning(f"faster-whisper on CUDA failed ({e}); using CPU")
    # multilingual base model: PS.md 6d wants Hindi explanations
    segments, _ = WhisperModel("base", device="cpu", compute_type="int8").transcribe(str(audio_path), vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


def has_video(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return out.returncode == 0 and "video" in out.stdout


def make_clips(source: Path, wav: Path, units: List[SourceUnit], out_dir: Path) -> Dict[str, Dict[str, str]]:
    """Cut each transcript unit's time span into a small silent video clip and a 16 kHz wav for embedding.

    Audio-only sources get the wav only. A unit whose cut fails is left out and embeds as text.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    video = has_video(source)
    media: Dict[str, Dict[str, str]] = {}
    for u in units:
        start = u.locator.t_start or 0.0
        dur = min((u.locator.t_end or start) - start, MAX_CLIP_SECONDS)
        if dur <= 0.5:
            continue
        span = ["-ss", f"{start:.2f}", "-t", f"{dur:.2f}"]
        entry = {"audio": str(out_dir / f"{u.unit_id}.wav")}
        cmds = [["ffmpeg", "-y", "-loglevel", "error", *span, "-i", str(wav), "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", entry["audio"]]]
        if video:
            entry["video"] = str(out_dir / f"{u.unit_id}.mp4")
            cmds.append(["ffmpeg", "-y", "-loglevel", "error", *span, "-i", str(source), "-an",
                         "-vf", "fps=2,scale=480:-2,format=yuv420p", entry["video"]])
        try:
            for cmd in cmds:
                subprocess.run(cmd, check=True, capture_output=True)
            if all(Path(p).stat().st_size > 1024 for p in entry.values()):  # an empty clip would fail the whole embed batch
                media[u.unit_id] = entry
            else:
                logger.warning(f"clip for {u.unit_id} is empty; embedding text only")
        except subprocess.CalledProcessError as e:
            logger.warning(f"clip for {u.unit_id} failed: {e.stderr.decode()[:200]}")
    return media


def match_slides(source: Path, source_id: str) -> None:
    """Find keyframes, OCR them, match them to slide pages, and copy the match into the Qdrant payloads."""
    import store
    from config import COLLECTION
    from ingest.scenes import extract_scenes
    from ingest.slide_match import match_video_to_slides

    match_video_to_slides(source_id, extract_scenes(source, source_id))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT unit_id, matched_slide_page, image_path FROM units WHERE source_id=? AND matched_slide_page IS NOT NULL",
            (source_id,),
        ).fetchall()
    finally:
        conn.close()
    client = store.client()
    for uid, page, image in rows:
        client.set_payload(COLLECTION, payload={"matched_slide_page": page, "image": image}, points=[store._point_id(uid)])

def _unit_id(source_id: str, t_start: float) -> str:
    """Stable per (source, start time), so re-ingesting a source replaces its units instead of duplicating them."""
    return "u_" + hashlib.sha1(f"{source_id}:{t_start:.2f}".encode()).hexdigest()[:8]


def chunk_transcript(source_id: str, title: str, segments: List[Dict[str, Any]], target_duration: int = 30) -> List[SourceUnit]:
    """
    Chunks transcript segments into validated SourceUnits (approx target_duration seconds).
    Aligns on segment boundaries to maintain semantic cohesion.
    """
    units = []
    current_chunk_text = []
    current_start = None
    current_end = None
    
    for seg in segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))
        text = str(seg.get("text", "")).strip()
        
        if current_start is None:
            current_start = seg_start
        
        current_chunk_text.append(text)
        current_end = seg_end
        
        if (current_end - current_start) >= target_duration:
            unit_id = _unit_id(source_id, current_start)
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
            
    if current_chunk_text and current_start is not None:
        unit_id = _unit_id(source_id, current_start)
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

def process_video(job_id: str, source_id: str, source_path_or_url: str) -> None:
    """Main Orchestrator for Task B1: YouTube/Local Video Ingestion"""
    temp_audio = MEDIA_DIR / "temp" / f"{job_id}_{source_id}.wav"
    temp_dl = MEDIA_DIR / "temp" / f"{job_id}_{source_id}_dl.tmp"
    clip_dir = MEDIA_DIR / "temp" / f"{job_id}_{source_id}_clips"

    try:
        # 1. Fetch Source Metadata
        conn = get_connection()
        row = conn.execute("SELECT title FROM sources WHERE source_id=?", (source_id,)).fetchone()
        conn.close()
        
        if not row:
            raise ValueError(f"Source ID {source_id} not found in database.")
        title = row[0]
        
        # 2. Handle YouTube vs Local
        if source_path_or_url.startswith("http://") or source_path_or_url.startswith("https://"):
            jobs.update_job_progress(job_id, stage='downloading', progress=0.1)
            download_youtube_audio(source_path_or_url, temp_dl)
            local_source = temp_dl
        else:
            local_source = Path(source_path_or_url)
            if not local_source.exists():
                raise FileNotFoundError(f"Local file {local_source} not found.")

        # 3. Extract Audio (FFmpeg)
        jobs.update_job_progress(job_id, stage='extracting_audio', progress=0.3)
        extract_audio(local_source, temp_audio)
        
        # 4. Transcribe & VAD (Whisper Server)
        jobs.update_job_progress(job_id, stage='transcribing', progress=0.6)
        segments = get_transcript(temp_audio)
        
        # 5. Chunking (SourceUnit mapping)
        jobs.update_job_progress(job_id, stage='chunking', progress=0.9)
        units = chunk_transcript(source_id, title, segments, target_duration=30)
        
        # 6. Persist Units (SQLite + Qdrant fallback logic); each unit embeds its clip's frames and speech with the text
        media = make_clips(local_source, temp_audio, units, clip_dir)
        write_units(units, media=media)

        # 6b. Keyframes -> OCR -> slide match. Best effort: the transcript units are already searchable.
        if has_video(local_source):
            try:
                match_slides(local_source, source_id)
            except Exception as e:
                logger.warning(f"Slide matching skipped: {e}")

        # 7. Mark Job & Source Complete
        jobs.update_job_progress(job_id, stage='completed', progress=1.0, units_created=len(units))
        
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
        if temp_dl.exists():
            temp_dl.unlink()
        shutil.rmtree(clip_dir, ignore_errors=True)
