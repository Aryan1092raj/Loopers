"""Serve api.main:app on a Lightning Studio GPU and expose it on a fixed ngrok domain.

Run inside the Studio, from RAG_DB:  python scripts/lightning_serve.py
Reads from the environment: NGROK_AUTHTOKEN, NGROK_DOMAIN, QDRANT_URL, QDRANT_API_KEY, DEEPSEEK_API_KEY, HF_TOKEN,
and optionally GROQ_API_KEY, GEMINI_API_KEY, API_CORS_ORIGINS. Stop it with Ctrl+C.
"""
import os
import subprocess
import sys
import time

import requests
from pyngrok import ngrok

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["NGROK_AUTHTOKEN", "NGROK_DOMAIN", "QDRANT_URL", "QDRANT_API_KEY", "DEEPSEEK_API_KEY", "HF_TOKEN"]
missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    sys.exit(f"Missing environment variables: {', '.join(missing)}")

env = {
    **os.environ,
    "LOOPERS_NO_REEXEC": "1",
    "MPLBACKEND": "Agg",
    "COLLECTION": os.environ.get("COLLECTION", "multimodal_hackathon"),
    "EMBED_MODEL": "google/embeddinggemma-2",
}


def start_api():
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"],
        cwd=ROOT, env=env,
    )


def wait_healthy(timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get("http://localhost:8000/api/health", timeout=3).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    return False


api = start_api()
if not wait_healthy():
    api.terminate()
    sys.exit("API did not become healthy within 5 minutes")

ngrok.set_auth_token(os.environ["NGROK_AUTHTOKEN"])
ngrok.connect(8000, domain=os.environ["NGROK_DOMAIN"])
public = f"https://{os.environ['NGROK_DOMAIN']}"
print(f"Endpoint: {public}  (send header ngrok-skip-browser-warning: 1 from the frontend proxy)", flush=True)

try:
    while True:
        if api.poll() is not None:  # ponytail: restart on crash, no backoff; add one if it crash-loops
            print("api exited, restarting", flush=True)
            api = start_api()
            wait_healthy()
        time.sleep(30)
except KeyboardInterrupt:
    api.terminate()
    ngrok.kill()
