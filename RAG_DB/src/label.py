"""Phase 3 — Image labeling via OpenRouter vision models (parallel).

Every figure in processed/images/ is sent to an OpenRouter vision model, which
returns a caption, a type classification, keywords, and (for handwritten/equation
figures) a transcription. Results land in metadata/labels.json. Transcriptions
are appended back into the parent .md so they become searchable text.

Idempotent: images already present in labels.json are skipped.

PARALLEL EXECUTION:
  Uses ThreadPoolExecutor (API calls are I/O-bound, not CPU-bound).
  max_workers=4 is safe on OpenRouter free tier; paid tier can go higher.
  A threading.Lock protects labels.json writes so results are saved immediately
  after each image — a crash mid-run loses no already-labeled images.
"""
import base64
import json
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from llm import label_image_with_vision
from config import IMAGES, PROCESSED, LABELS, METADATA

IMAGE_TYPES = ["diagram", "graph", "photo", "handwritten", "equation"]

PROMPT = f"""You are labeling an academic figure for a searchable knowledge base.
Respond with ONLY a JSON object, no prose, matching:
{{
  "caption": "2-3 sentence description: what the figure shows, what concept it illustrates, what a student should learn from it",
  "type": one of ["diagram", "graph", "photo", "handwritten", "equation"],
  "keywords": ["5-10 lowercase search keywords covering the subject, concept, and visible labels"],
  "transcription": "ALL visible text, labels, axis names, variable names, and equations inside the figure; empty string only if truly nothing is readable"
}}"""

# Max concurrent API calls. 1 for local GPU (thread-safe), 4 for API-based labeling.
import os as _os
MAX_WORKERS = 1 if _os.getenv("USE_GPU_OCR", "0") == "1" else 4

_labels_lock = threading.Lock()


def _b64(path: Path) -> tuple[str, str]:
    media = mimetypes.guess_type(path)[0] or "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return media, data


def _parse_json(text: str) -> dict:
    text = text.strip()
    import re
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    if text.startswith("```"):
        text = text.split("```", 2)[1].removeprefix("json").strip()
    return json.loads(text)


def load_labels() -> dict:
    if LABELS.exists():
        return json.loads(LABELS.read_text())
    return {}


def save_labels(labels: dict) -> None:
    METADATA.mkdir(parents=True, exist_ok=True)
    LABELS.write_text(json.dumps(labels, indent=2), encoding="utf-8")


def label_image(path: Path) -> dict:
    # Use specialized prompts depending on the type detected by PPStructure
    if "_equation_" in path.name:
        prompt = """You are transcribing a mathematical equation from an academic document.
Respond with ONLY a JSON object, no prose, matching:
{
  "caption": "2-3 sentence description: what this equation/formula represents, what quantities are involved, and when it is used",
  "type": "equation",
  "keywords": ["3-7 lowercase search keywords for the equation and its domain"],
  "transcription": "verbatim LaTeX code of the equation, enclosed in $$...$$; include ALL terms and variables"
}"""
    elif "_figure_" in path.name:
        prompt = """You are labeling an academic diagram/figure (possibly a TikZ/vector graphic).
Respond with ONLY a JSON object, no prose, matching:
{
  "caption": "3-4 sentence description: what the diagram depicts, what concept it explains, the key components shown, and what a student should understand from it",
  "type": "diagram",
  "keywords": ["6-12 specific lowercase search keywords covering subject, components, and concept"],
  "transcription": "ALL visible text, axis labels, variable names, component names, numbers, and equations inside the diagram"
}"""
    else:
        prompt = PROMPT

    text = label_image_with_vision(path, prompt)
    result = _parse_json(text)

    # Quality gate: if caption is too short, retry once with explicit instruction
    if len(result.get("caption", "")) < 20 or not result.get("keywords"):
        retry_prompt = prompt + "\n\nIMPORTANT: Your previous response had an insufficient caption or missing keywords. Write a detailed caption of at least 2 sentences and include at least 5 keywords."
        try:
            text2 = label_image_with_vision(path, retry_prompt)
            result2 = _parse_json(text2)
            if len(result2.get("caption", "")) > len(result.get("caption", "")):
                result = result2
        except Exception:
            pass  # keep original if retry fails

    return result


def _parent_md(image_name: str) -> Path | None:
    """Figure name is <stem>_p<n>_<i>.png or <stem>.<ext>; parent md is <stem>.md."""
    if "/" in image_name:  # <stem>/<file>: extract writes figures per document
        stem = image_name.split("/")[0]
    else:
        stem = image_name.split("_p")[0] if "_p" in image_name else Path(image_name).stem
    md = PROCESSED / f"{stem}.md"
    return md if md.exists() else None


def _label_one(img: Path, labels: dict) -> tuple[str, dict | None, str]:
    """Worker: label a single image. Returns (img.name, result_or_None, error_msg)."""
    key = img.relative_to(IMAGES).as_posix()
    try:
        result = label_image(img)
        result["image"] = str(img.relative_to(PROCESSED))
        return key, result, ""
    except Exception as e:  # noqa: BLE001
        return key, None, str(e)


def run() -> None:
    labels = load_labels()

    imgs = [
        p for p in sorted(IMAGES.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    ]
    if not imgs:
        print("[label] no images — run extract first")
        return

    todo = [
        img for img in imgs
        if img.relative_to(IMAGES).as_posix() not in labels
        and "_equation_" not in img.name  # equations → LaTeX text, skip Moondream
    ]
    if not todo:
        print(f"[label] all {len(imgs)} image(s) already labeled — nothing to do")
        return

    print(f"[label] labeling {len(todo)} image(s) with {MAX_WORKERS} parallel workers...")
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_label_one, img, labels): img for img in todo}

        for future in as_completed(futures):
            name, result, err = future.result()

            if result is None:
                print(f"[label] {name} failed: {err}")
                continue

            # Thread-safe: lock, update in memory, write to disk immediately
            with _labels_lock:
                labels[name] = result
                save_labels(labels)

            done += 1
            print(f"[label] {name}: {result.get('type')} — {result.get('caption', '')[:60]}")

            # Append transcription to parent markdown (thread-safe per-file write)
            transcription = result.get("transcription", "").strip()
            if transcription:
                md = _parent_md(name)
                if md:
                    # Read-modify-write under lock to avoid races on same parent md
                    with _labels_lock:
                        md.write_text(
                            md.read_text(encoding="utf-8")
                            + f"\n\n<!-- transcription of {name} -->\n{transcription}\n",
                            encoding="utf-8",
                        )

    print(f"[label] {done} newly labeled, {len(labels)} total")


if __name__ == "__main__":
    run()
