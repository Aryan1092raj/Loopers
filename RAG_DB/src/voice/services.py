"""Speech to text through the Whisper server (local faster-whisper fallback) and text to speech with Piper."""
import hashlib
import os
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

from ingest.video_worker import get_transcript

VOICE = os.getenv("PIPER_VOICE", "en_US-lessac-medium")
VOICE_DIR = Path(os.getenv("HF_HOME", Path.home() / ".cache")) / "piper"


def transcribe_audio(file_path: str) -> dict:
    """{"text", "language", "duration"}; raises when neither the server nor the local model can transcribe."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file {file_path} not found.")
    segments = get_transcript(Path(file_path))
    return {
        "text": " ".join(s["text"].strip() for s in segments).strip(),
        "language": None,  # ponytail: the server reply carries it; add when the UI needs it
        "duration": segments[-1]["end"] if segments else 0.0,
    }


@lru_cache(maxsize=1)
def _voice():
    from piper import PiperVoice

    model = VOICE_DIR / f"{VOICE}.onnx"
    if not model.exists():  # first call downloads about 60 MB into the model volume
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(VOICE_DIR), VOICE], check=True)
    return PiperVoice.load(model)


def synthesize_speech(text: str, output_dir: Path) -> Path:
    """Speak `text` to a cached wav named by its hash."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{hashlib.md5(f'{VOICE}:{text}'.encode()).hexdigest()}.wav"
    if not output_path.exists():
        tmp = output_path.with_suffix(".part")
        with wave.open(str(tmp), "wb") as f:
            _voice().synthesize_wav(text, f)
        tmp.rename(output_path)
    return output_path
