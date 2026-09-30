"""GLM-OCR parser wrapper (Phase 3 addition).

GLM-OCR is a 0.9B multimodal OCR model by Z.ai.
Supports local SDK execution (requires `pip install glmocr`)
and remote/local OpenAI-compatible API execution (e.g., Ollama or vLLM).
"""
import base64
import os
from pathlib import Path
import tempfile
import requests
import fitz

from config import (
    GLM_OCR_MODE,
    GLM_OCR_API_URL,
    GLM_OCR_API_KEY,
    GLM_OCR_MODEL,
    OCR_ZOOM
)


def parse(pdf_path: str | Path) -> str | None:
    """Parse a PDF file using GLM-OCR (local or remote).

    Returns:
        The extracted Markdown text, or None on failure.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return None

    if GLM_OCR_MODE == "local":
        return _parse_local(pdf_path)
    else:
        return _parse_remote(pdf_path)


def _parse_local(pdf_path: Path) -> str | None:
    """Parse using local glmocr SDK."""
    try:
        from glmocr import parse as local_parse
    except ImportError:
        print("[parsers/glmocr] Error: `glmocr` Python SDK not installed. Run `pip install glmocr`.")
        return None

    try:
        # Create a temporary directory for local parse results
        with tempfile.TemporaryDirectory() as temp_dir:
            result = local_parse(str(pdf_path))
            result.save(output_dir=temp_dir)
            
            # Find the saved markdown file in output
            temp_path = Path(temp_dir)
            md_files = list(temp_path.glob("*.md"))
            if md_files:
                return md_files[0].read_text(encoding="utf-8")
                
            # Fallback check dict
            try:
                data = result.to_dict()
                if isinstance(data, dict) and "markdown" in data:
                    return data["markdown"]
            except Exception:
                pass
                
            return None
    except Exception as exc:
        print(f"[parsers/glmocr] Local parse error for {pdf_path}: {exc}")
        return None


def _parse_remote(pdf_path: Path) -> str | None:
    """Parse using remote/local OpenAI-compatible API (Ollama/vLLM/ZhipuAI)."""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        print(f"[parsers/glmocr] Fitz open failed for {pdf_path}: {exc}")
        return None

    markdown_pages = []

    try:
        for page_idx in range(doc.page_count):
            page = doc.load_page(page_idx)
            # Render page to image bytes using current zoom setting
            zoom = OCR_ZOOM
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes(format="png")
            
            # Base64 encode
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            
            # Call API
            headers = {
                "Content-Type": "application/json"
            }
            if GLM_OCR_API_KEY and GLM_OCR_API_KEY != "EMPTY":
                headers["Authorization"] = f"Bearer {GLM_OCR_API_KEY}"
                
            payload = {
                "model": GLM_OCR_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Transcribe this document page into layout-preserved Markdown. Do not include conversation, greeting, or code fences."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_b64}"
                                }
                            }
                        ]
                    }
                ],
                "temperature": 0.1
            }
            
            url = f"{GLM_OCR_API_URL.rstrip('/')}/chat/completions"
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            
            data = resp.json()
            page_text = data["choices"][0]["message"]["content"]
            markdown_pages.append(f"<!-- Page {page_idx + 1} -->\n{page_text}")

        doc.close()
        return "\n\n".join(markdown_pages)

    except Exception as exc:
        print(f"[parsers/glmocr] Remote parse failed for {pdf_path}: {exc}")
        if 'doc' in locals():
            doc.close()
        return None
