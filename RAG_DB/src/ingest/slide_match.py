import logging
from rapidfuzz import fuzz
from core.db import get_connection

logger = logging.getLogger(__name__)

def match_video_to_slides(source_id: str, scenes: list[dict]):
    """
    Correlates extracted keyframe OCR text with existing slide text units.
    Applies temporal monotonicity to reject backward jumps unless highly confident.
    """
    conn = get_connection()
    try:
        video_units = conn.execute(
            "SELECT unit_id, t_start, t_end FROM units WHERE source_id=? AND kind='transcript' ORDER BY t_start", 
            (source_id,)
        ).fetchall()
        
        slide_units = conn.execute(
            "SELECT page_no, text FROM units WHERE kind IN ('text', 'figure') AND page_no IS NOT NULL ORDER BY page_no"
        ).fetchall()
        
        if not slide_units:
            logger.warning("No slide pages found to match against.")
            return

        last_matched_page = 0
        
        for v_unit_id, t_start, t_end in video_units:
            if t_start is None:
                continue

            active_scene = None
            for s in scenes:
                # Proper inclusive-exclusive boundary check
                if s["start"] <= t_start < s["end"]:
                    active_scene = s
                    break
            
            if not active_scene:
                past_scenes = [s for s in scenes if s["end"] <= t_start]
                if past_scenes:
                    active_scene = past_scenes[-1]
                elif scenes:
                    active_scene = scenes[-1] if t_start > scenes[-1]["end"] else scenes[0]
            
            if not active_scene:
                continue
                
            ocr_text = active_scene.get("ocr_text", "")
            if not ocr_text:
                continue
            
            best_page = None
            best_score = 0.0
            
            for page_no, slide_text in slide_units:
                score = fuzz.token_set_ratio(ocr_text, slide_text)
                
                if page_no < last_matched_page:
                    if score < 90.0:
                        score -= 25.0
                
                if score > best_score:
                    best_score = score
                    best_page = page_no
                    
            if best_page is not None and best_score > 55.0:
                last_matched_page = best_page
                conn.execute(
                    "UPDATE units SET matched_slide_page=?, image_path=? WHERE unit_id=?",
                    (best_page, str(active_scene["image_path"]), v_unit_id)
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to match slides to video units: {e}")
        raise
    finally:
        conn.close()
