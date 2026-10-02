import os
import subprocess
import threading
from pathlib import Path

# Try to import optional packages gracefully
try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

class AudioProcessor:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def clean_audio(self, input_path: Path, output_path: Path, denoise: bool = True) -> bool:
        """Converts input audio/video to WAV 16kHz mono, and optionally denoises it."""
        if denoise:
            # afftdn: FFT denoiser, highpass/lowpass filters for human speech band (200-3000 Hz)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-af", "highpass=f=200, lowpass=f=3000, afftdn",
                "-ar", "16000",
                "-ac", "1",
                str(output_path)
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-ar", "16000",
                "-ac", "1",
                str(output_path)
            ]
        
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return True
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg error: {e.stderr.decode()}")
            return False

    def download_youtube_audio(self, url: str) -> Path:
        """Downloads YouTube audio and converts it to WAV."""
        if not yt_dlp:
            raise ImportError("yt-dlp is not installed.")

        out_template = str(self.output_dir / "yt_download_%(id)s.%(ext)s")
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': '192',
            }],
            'quiet': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            wav_path = Path(os.path.splitext(filename)[0] + ".wav")
            return wav_path

    def transcribe_local_whisper(self, audio_path: Path, model_size: str = "medium") -> str:
        """Transcribes audio file using faster-whisper locally."""
        if not WhisperModel:
            raise ImportError("faster-whisper is not installed.")

        # Default to CUDA for RTX 4050, fallback to CPU
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            # WhisperModel expects model size (e.g. 'medium', 'large-v3')
            # For 6GB VRAM, float16 is suitable
            model = WhisperModel(model_size, device=device, compute_type="float16")
        except Exception as e:
            print(f"CUDA failed: {e}. Falling back to CPU...")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

        segments, info = model.transcribe(str(audio_path), beam_size=5)
        text_segments = [seg.text for seg in segments]
        return " ".join(text_segments)

    def transcribe_groq_whisper(self, audio_path: Path, api_key: str) -> str:
        """Transcribes audio file using Groq Whisper API (highly recommended for speed)."""
        # If file is larger than 24MB, chunk it using ffmpeg segmenting
        max_bytes = 24 * 1024 * 1024
        file_size = audio_path.stat().st_size
        
        if file_size > max_bytes and not audio_path.name.startswith("groq_temp_chunk_"):
            print(f"[Groq Whisper] File size {file_size / (1024*1024):.1f}MB exceeds 24MB. Auto-chunking...")
            chunk_pattern = self.output_dir / "groq_temp_chunk_%03d.wav"
            cmd = [
                "ffmpeg", "-y",
                "-i", str(audio_path),
                "-f", "segment",
                "-segment_time", "600",
                "-c", "copy",
                str(chunk_pattern)
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                chunks = sorted(self.output_dir.glob("groq_temp_chunk_*.wav"))
                print(f"[Groq Whisper] Generated {len(chunks)} chunks.")
                
                texts = []
                for i, chunk in enumerate(chunks):
                    print(f"[Groq Whisper] Transcribing chunk {i+1}/{len(chunks)} ({chunk.name})...")
                    # Recursive call on the smaller chunk
                    chunk_text = self.transcribe_groq_whisper(chunk, api_key)
                    if chunk_text.strip():
                        texts.append(chunk_text.strip())
                
                # Clean up chunks
                for chunk in chunks:
                    try:
                        chunk.unlink()
                    except Exception:
                        pass
                        
                return " ".join(texts)
            except Exception as e:
                # Clean up chunks on error
                for chunk in self.output_dir.glob("groq_temp_chunk_*.wav"):
                    try:
                        chunk.unlink()
                    except Exception:
                        pass
                raise Exception(f"Failed to transcribe chunked audio: {e}")

        import httpx
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            data = {"model": "whisper-large-v3", "response_format": "json"}
            # Audio files can be large, use 120s timeout
            response = httpx.post(url, headers=headers, data=data, files=files, timeout=120.0)
            
        if response.status_code == 200:
            return response.json().get("text", "")
        else:
            raise Exception(f"Groq API Error ({response.status_code}): {response.text}")

    def transcribe_granite_speech(self, audio_path: Path) -> str:
        """Transcribes audio file using Granite Speech via transformers pipeline."""
        import torch
        try:
            from transformers import pipeline
        except ImportError:
            raise ImportError("transformers is not installed. Run `pip install transformers accelerate` to use Granite Speech.")

        model_id = "ibm-granite/granite-speech-3.3-8b"
        print(f"Loading Granite Speech model '{model_id}'...")
        
        # Load in float16 with device_map="auto" to use RTX 4050 efficiently
        pipe = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            torch_dtype=torch.float16,
            device_map="auto"
        )
        
        result = pipe(str(audio_path))
        return result.get("text", "")
