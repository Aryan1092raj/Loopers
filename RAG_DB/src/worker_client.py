"""Calls from the Render API to the Lightning GPU worker. WORKER_URL unset means models run in this process."""
import os

import httpx

WORKER_URL = os.getenv("WORKER_URL", "").rstrip("/")


class WorkerOffline(RuntimeError):
    pass


def call(method: str, path: str, timeout: float = 120.0, **kw) -> httpx.Response:
    headers = {"X-Worker-Key": os.getenv("WORKER_KEY", ""), "ngrok-skip-browser-warning": "1"}
    try:
        r = httpx.request(method, WORKER_URL + path, headers=headers, timeout=timeout, **kw)
    except httpx.HTTPError as e:
        raise WorkerOffline(f"GPU worker offline: {e}") from e
    # ngrok answers an HTML page, not our JSON, when the tunnel has no Studio behind it.
    if r.status_code >= 502 or (r.headers.get("content-type", "").startswith("text/html") and "ERR_NGROK" in r.text[:2000]):
        raise WorkerOffline(f"GPU worker offline (HTTP {r.status_code})")
    return r


def embed(texts: list[str], is_query: bool) -> tuple[list[list[float]], list[dict] | None]:
    r = call("POST", "/worker/embed", json={"texts": texts, "is_query": is_query})
    r.raise_for_status()
    d = r.json()
    return d["dense"], d["sparse"]


def rerank(query: str, texts: list[str]) -> list[float]:
    r = call("POST", "/worker/rerank", json={"query": query, "texts": texts})
    r.raise_for_status()
    return r.json()["scores"]
