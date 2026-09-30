import config
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=1)
def _engine():
    from paddleocr import PaddleOCR
    import paddle
    import os
    use_gpu = os.getenv("USE_GPU_OCR", "1" if paddle.device.is_compiled_with_cuda() else "0") == "1"
    return PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=use_gpu)


@lru_cache(maxsize=1)
def _structure_engine():
    from paddleocr import PPStructure
    import paddle
    import os
    use_gpu = os.getenv("USE_GPU_OCR", "1" if paddle.device.is_compiled_with_cuda() else "0") == "1"
    # formula=False to prevent Paddle tensor cast error, table=True for SLANet
    return PPStructure(formula=False, table=True, layout=True, ocr=True, show_log=False, use_gpu=use_gpu)


def ocr_array(arr) -> str:
    """OCR a HxWx3 uint8 numpy array -> newline-joined recognized text."""
    result = _engine().ocr(arr, cls=True)
    lines = []
    for page in result or []:
        for item in page or []:
            # item = [box, (text, confidence)]
            lines.append(item[1][0])
    return "\n".join(lines)


def ocr_path(path: Path) -> str:
    import numpy as np
    from PIL import Image

    arr = np.array(Image.open(path).convert("RGB"))
    return ocr_array(arr)


def structure_analyze(arr) -> list:
    """Run PPStructure layout analysis on a HxWx3 uint8 numpy array."""
    return _structure_engine()(arr)


@lru_cache(maxsize=1)
def _formula_engine():
    """Lazy-init PP-FormulaNet. Returns None if it crashes (known tensor cast issue)."""
    try:
        from paddleocr import PaddleOCR
        import paddle
        import os
        use_gpu = os.getenv("USE_GPU_OCR", "1" if paddle.device.is_compiled_with_cuda() else "0") == "1"
        engine = PaddleOCR(formula=True, show_log=False, use_gpu=use_gpu)
        return engine
    except Exception as e:
        print(f"[ocr] PP-FormulaNet init failed ({e}), formula recognition unavailable")
        return None


def formula_recognize(arr) -> str:
    """Recognize mathematical formula from image array, return LaTeX string.

    Uses PP-FormulaNet if available, otherwise returns empty string.
    Caller should fall back to ocr_array() for plain text extraction.
    """
    engine = _formula_engine()
    if engine is None:
        return ""
    try:
        result = engine.ocr(arr, cls=False)
        if result and result[0]:
            texts = [item[1][0] for item in result[0] if item[1][1] > 0.5]
            return ' '.join(texts)
    except Exception as e:
        print(f"[ocr] Formula recognition failed: {e}")
    return ""

