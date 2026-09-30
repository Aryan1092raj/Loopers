#!/usr/bin/env python3
"""
Local Whisper Large V3 Turbo — OpenAI-compatible transcription server.

Exposes POST /v1/audio/transcriptions that accepts multipart/form-data with
the same fields as OpenAI's Whisper API, so the Groq SDK (or any
OpenAI-compatible client) can hit it transparently.

Usage:
    conda activate whisper
    python server.py                        # default: 0.0.0.0:8300, float16
    python server.py --port 9000            # custom port
    python server.py --device cpu           # CPU fallback (slow)

The model is downloaded from Hugging Face on first run (~1.5 GB) and cached.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# ── Auto-inject PyPI CUDA libraries into LD_LIBRARY_PATH ───────────────
import site
site_pkgs = site.getsitepackages()[0]
nvidia_lib_dirs = [
    os.path.join(site_pkgs, "nvidia", "cublas", "lib"),
    os.path.join(site_pkgs, "nvidia", "cudnn", "lib"),
]
current_ld = os.environ.get("LD_LIBRARY_PATH", "")
missing_dirs = [d for d in nvidia_lib_dirs if d not in current_ld and os.path.exists(d)]
if missing_dirs:
    os.environ["LD_LIBRARY_PATH"] = ":".join(missing_dirs + ([current_ld] if current_ld else []))
    print("[whisper-server] Auto-injected PyPI CUDA libraries into LD_LIBRARY_PATH. Re-executing...")
    os.execv(sys.executable, [sys.executable] + sys.argv)
# ────────────────────────────────────────────────────────────────────────

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# ── Model loading (done once at startup) ──────────────────────────────────────

# Global — loaded in lifespan, used by the endpoint.
_model = None
_model_id = None

DEFAULT_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"


def load_model(model_id: str, device: str, compute_type: str):
    """Load (or download) a CTranslate2 Whisper model via faster-whisper."""
    from faster_whisper import WhisperModel

    global _model, _model_id
    print(f"[whisper-server] Loading model: {model_id}")
    print(f"[whisper-server] Device: {device}, Compute type: {compute_type}")
    t0 = time.time()
    _model = WhisperModel(model_id, device=device, compute_type=compute_type)
    _model_id = model_id
    print(f"[whisper-server] Model loaded in {time.time() - t0:.1f}s")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Local Whisper Server", version="1.0.0")


@app.get("/v1/models")
@app.get("/openai/v1/models")
@app.get("/models")
async def list_models():
    """OpenAI-compatible GET /v1/models — lets clients discover what's loaded."""
    return {
        "object": "list",
        "data": [
            {
                "id": _model_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local",
            }
        ],
    }


# The Groq SDK appends /openai/v1/audio/transcriptions to baseURL.
# The OpenAI SDK appends /audio/transcriptions to baseURL.
# Accept all common path patterns so this works with any client.
@app.post("/v1/audio/transcriptions")
@app.post("/openai/v1/audio/transcriptions")
@app.post("/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default=None),
    response_format: str = Form(default="json"),
    language: str = Form(default=None),
    prompt: str = Form(default=None),
    temperature: float = Form(default=0.0),
):
    """
    OpenAI-compatible POST /v1/audio/transcriptions.

    Accepts the same multipart fields the Groq/OpenAI SDK sends:
      - file:             audio file (mp3, wav, m4a, webm, …)
      - model:            ignored (we use whatever is loaded), but accepted for compat
      - response_format:  "text" | "json" | "verbose_json" | "srt" | "vtt"
      - language:         ISO-639-1 code (optional, auto-detect if omitted)
      - prompt:           initial prompt / context for the decoder
      - temperature:      sampling temperature (0 = greedy)
    """
    if _model is None:
        raise HTTPException(503, "Model not loaded yet — try again shortly.")

    # Read uploaded audio into a temporary file (faster-whisper needs a path or
    # file-like object, but some formats need seeking which UploadFile doesn't
    # support well — a tmpfile is safest).
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file.")

    suffix = Path(file.filename or "audio.mp3").suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()

        t0 = time.time()
        segments, info = _model.transcribe(
            tmp.name,
            language=language or None,
            initial_prompt=prompt or None,
            temperature=temperature,
            beam_size=5,
            vad_filter=True,        # skip silence — faster + cleaner
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        # Materialise all segments (the iterator is lazy).
        all_segments = []
        full_text_parts = []
        for seg in segments:
            all_segments.append(seg)
            full_text_parts.append(seg.text)

        full_text = "".join(full_text_parts).strip()
        elapsed = time.time() - t0

    audio_duration = info.duration if info else 0
    speed = audio_duration / elapsed if elapsed > 0 else 0
    print(
        f"[whisper-server] Transcribed {file.filename} "
        f"({audio_duration:.0f}s audio) in {elapsed:.1f}s "
        f"({speed:.1f}x realtime), lang={info.language}"
    )

    # ── Format the response ───────────────────────────────────────────────────
    if response_format == "text":
        return PlainTextResponse(full_text)

    if response_format == "srt":
        return PlainTextResponse(_to_srt(all_segments), media_type="text/plain")

    if response_format == "vtt":
        return PlainTextResponse(_to_vtt(all_segments), media_type="text/plain")

    if response_format == "verbose_json":
        return JSONResponse(
            {
                "task": "transcribe",
                "language": info.language if info else "unknown",
                "duration": audio_duration,
                "text": full_text,
                "segments": [
                    {
                        "id": i,
                        "start": s.start,
                        "end": s.end,
                        "text": s.text,
                        "avg_logprob": s.avg_logprob,
                        "no_speech_prob": s.no_speech_prob,
                    }
                    for i, s in enumerate(all_segments)
                ],
            }
        )

    # Default: "json" — matches OpenAI's { "text": "..." }
    return JSONResponse({"text": full_text})


# ── Subtitle helpers ──────────────────────────────────────────────────────────

def _ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_ts(seg.start)} --> {_ts(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def _to_vtt(segments) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        t_start = _ts(seg.start).replace(",", ".")
        t_end = _ts(seg.end).replace(",", ".")
        lines.append(f"{t_start} --> {t_end}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


# ── Health / readiness ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "model": _model_id, "ready": _model is not None}


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Local Whisper transcription server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8300, help="Port (default: 8300)")
    parser.add_argument(
        "--model",
        default=os.environ.get("WHISPER_MODEL", DEFAULT_MODEL),
        help=f"Whisper model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("WHISPER_DEVICE", "cuda"),
        choices=["cuda", "cpu", "auto"],
        help="Device (default: cuda)",
    )
    parser.add_argument(
        "--compute-type",
        default=os.environ.get("WHISPER_COMPUTE_TYPE", "float16"),
        help="Compute type: float16, int8_float16, int8 (default: float16)",
    )
    args = parser.parse_args()

    # Load model before starting uvicorn so it's ready for the first request.
    load_model(args.model, args.device, args.compute_type)

    print(f"\n[whisper-server] Listening on http://{args.host}:{args.port}")
    print(f"[whisper-server] Set GROQ_BASE_URL=\"http://localhost:{args.port}\" (no /v1 suffix)\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
