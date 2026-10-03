import os
import cv2
import logging
from pathlib import Path
from scenedetect import open_video, SceneManager, ContentDetector

logger = logging.getLogger(__name__)

# OCR Engine Initialization (with robust fallback for sandboxed/headless environments)
try:
    from rapidocr_onnxruntime import RapidOCR
    ocr_engine = RapidOCR()
    def extract_ocr_text(image_path: Path) -> str:
        """Extracts structured text from an image using ONNX OCR."""
        result, _ = ocr_engine(str(image_path))
        if not result:
            return ""
        return " ".join([line[1] for line in result])
except ImportError:
    logger.warning("rapidocr_onnxruntime not available. OCR will fall back to mocked output.")
    def extract_ocr_text(image_path: Path) -> str:
        """Fallback mock OCR for missing ONNX dependencies."""
        return f"Mock OCR Text Extracted from {image_path.name}"

def extract_scenes(video_path: Path, source_id: str) -> list[dict]:
    """
    Scans a video for visual transitions using PySceneDetect.
    Extracts a high-res keyframe at the midpoint of each scene and runs OCR.
    """
    output_dir = Path(__file__).resolve().parent.parent.parent.parent / "processed" / "media" / "keyframes" / source_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Detect Scenes
    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=27.0))
    scene_manager.detect_scenes(video, show_progress=False)
    scenes = scene_manager.get_scene_list()
    
    # Context manager / clean closure for OpenCV
    cap = cv2.VideoCapture(str(video_path))
    results = []
    
    try:
        for scene in scenes:
            start_sec = scene[0].get_seconds()
            end_sec = scene[1].get_seconds()
            mid_sec = start_sec + (end_sec - start_sec) / 2.0
            
            # 2. Extract Keyframe safely
            cap.set(cv2.CAP_PROP_POS_MSEC, mid_sec * 1000.0)
            ret, frame = cap.read()
            
            if ret:
                frame_path = output_dir / f"frame_{int(mid_sec * 1000)}.jpg"
                cv2.imwrite(str(frame_path), frame)
                
                # 3. Extract Text via OCR
                ocr_text = extract_ocr_text(frame_path)
                
                results.append({
                    "start": start_sec,
                    "end": end_sec,
                    "mid": mid_sec,
                    "image_path": str(frame_path),
                    "ocr_text": ocr_text
                })
    finally:
        cap.release()
        
    return results
