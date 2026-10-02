"""Script to clean and structure raw lecture transcripts/PDFs into discrete subtopics.

Generates a database of concept-specific folders under processed/concepts/ containing:
  - content.md (formatted Markdown with LaTeX equations)
  - alignment.json (speaker transcript-to-visual mapping)
  - assignments.json (assignments, solutions, and project pool)
"""
import argparse
import json
import os
import re
from pathlib import Path

from config import ROOT, PROCESSED, RAW
import llm

CONCEPTS_DIR = PROCESSED / "concepts"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "concept"


SEGMENTER_PROMPT = """You are an expert curriculum designer and AI educational assistant.
Your task is to take a raw lecture transcript, textbook extract, or OCR markdown, segment it into a FEW key distinct pedagogical CONCEPTS or SUBTOPICS (maximum 3-4 concepts), and structure it.

CRITICAL CONSTRAINTS TO PREVENT TOKEN TRUNCATION:
- Maximum 3 or 4 concepts total.
- Keep the `content_markdown` concise, dense, and directly focused on the technical content. Avoid conversational fluff. Keep it under 450 words per concept.
- Provide exactly 1 highly relevant assignment or quiz question per concept. Keep the solution direct and compact.
- Keep transcript alignments concise (2-3 items per concept).

For each concept, you must produce:
1. A descriptive title and a slugified folder name.
2. Structured lecture notes in markdown format (content_markdown). All mathematical expressions, matrices, vectors, and equations MUST be formatted in LaTeX notation (e.g. $A\\mathbf{x} = \\mathbf{b}$ or $$A = L U$$).
3. Transcript Alignments: A list of mappings showing exactly how the speaker explained specific contexts. Each mapping contains:
   - context: What concept/subtopic is being discussed.
   - transcript_snippet: The verbatim or slightly cleaned speaker transcript snippet.
   - presentation_type: One of: blackboard_writing, slide, computer_code, graphical_animation, verbal_analogy.
   - visual_aids: Detail of what is shown on screen/drawn on the board.
4. Assignments Pool: A list of questions, solutions, and project pools related to this concept. Each contains:
   - question: The exercise/problem text (using LaTeX for formulas).
   - solution: Concise step-by-step rigorous solution.
   - type: One of: assignment, quiz, project.

Return a JSON array of objects representing these concepts. Match this exact JSON schema:
[
  {
    "concept_name": "slugified-name-here",
    "title": "Readable Concept Title",
    "content_markdown": "Dense markdown content...",
    "transcript_alignment": [
      {
        "context": "Context summary",
        "transcript_snippet": "Verbatim excerpt...",
        "presentation_type": "blackboard_writing",
        "visual_aids": "Description of drawing/writing"
      }
    ],
    "assignments": [
      {
        "question": "Question text...",
        "solution": "Detailed solution...",
        "type": "assignment"
      }
    ]
  }
]

Do not return any conversational text, explanation, or markdown wrappers like ```json. Return ONLY the raw JSON array.
"""


def process_text_into_concepts(text: str) -> list[dict]:
    """Sends raw text to an LLM to segment into concepts, returning parsed dicts."""
    messages = [
        {"role": "system", "content": SEGMENTER_PROMPT},
        {"role": "user", "content": f"INPUT TEXT:\n\n{text[:12000]}"}  # Capped to avoid extreme VRAM/token cost
    ]
    
    response = ""
    # Try Nvidia NIM first, fallback to Gemini, then Groq
    import config
    if config.NVIDIA_API_KEY and config.NVIDIA_API_KEY != "YOUR_NVIDIA_KEY":
        try:
            print(f"[process_transcripts] Using Nvidia NIM ({llm.NVIDIA_MODEL})...")
            response = llm.call_nvidia_nim(messages, model=llm.NVIDIA_MODEL, max_tokens=4090)
        except Exception as e:
            print(f"[process_transcripts] NIM failed: {e}. Trying Gemini...")
            
    if not response:
        try:
            print("[process_transcripts] Using Gemini (gemini-2.5-flash)...")
            response = llm.call_gemini(messages, model="gemini-2.5-flash", max_tokens=8192)
        except Exception as e:
            print(f"[process_transcripts] Gemini failed: {e}. Trying Groq...")
            try:
                response = llm.call_groq(messages, model="llama-3.3-70b-versatile", max_tokens=3000)
            except Exception as ex:
                raise RuntimeError(f"All LLMs failed: {ex}")

    response = response.strip()
    # Strip markdown block if returned
    if response.startswith("```"):
        lines = response.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        response = "\n".join(lines).strip()

    # Repair unescaped backslashes and control characters in JSON strings
    try:
        from generate import fix_json_escapes
        response = fix_json_escapes(response)
    except Exception:
        pass

    try:
        data = json.loads(response)
        if not isinstance(data, list):
            raise ValueError("LLM did not return a list.")
        return data
    except Exception as e:
        print("[process_transcripts] Failed to parse JSON response from LLM.")
        print(f"[process_transcripts] Raw response was:\n{response}")
        raise e


def save_concepts(concepts: list[dict], source_file: str) -> list[Path]:
    """Saves structured concepts under processed/concepts/<slug>/"""
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths = []
    
    for c in concepts:
        slug = slugify(c.get("concept_name", c.get("title", "concept")))
        dest = CONCEPTS_DIR / slug
        dest.mkdir(parents=True, exist_ok=True)
        
        # 1. content.md
        md_file = dest / "content.md"
        content_header = f"# {c.get('title', slug)}\n\n*Source: {source_file}*\n\n"
        md_file.write_text(content_header + c.get("content_markdown", ""), encoding="utf-8")
        
        # 2. alignment.json
        alignment_file = dest / "alignment.json"
        alignment_file.write_text(json.dumps(c.get("transcript_alignment", []), indent=2), encoding="utf-8")
        
        # 3. assignments.json
        assignments_file = dest / "assignments.json"
        assignments_file.write_text(json.dumps(c.get("assignments", []), indent=2), encoding="utf-8")
        
        saved_paths.append(dest)
        print(f"[process_transcripts] Structured concept saved to: {dest.relative_to(ROOT)}")
        
    return saved_paths


def clean_transcript(text: str) -> str:
    """Remove filler words and diarization markers from raw transcript text.

    Called before LLM segmentation to reduce noise and token cost.
    Primary model is faster-whisper (word-level timestamps), which can produce
    filler words and occasional diarization markers.
    """
    # Strip speaker diarization markers: SPEAKER_0:, Speaker 1:, [SPEAKER_0], etc.
    text = re.sub(r"\[SPEAKER_\d+\]", "", text)
    text = re.sub(r"SPEAKER_\d+:", "", text)
    text = re.sub(r"Speaker\s+\d+:", "", text)

    # Remove filler words (word-boundary, case-insensitive)
    _FILLERS = r"\b(um+|uh+|like|you know|so basically|basically|right so|okay so|you see|i mean)\b"
    text = re.sub(_FILLERS, "", text, flags=re.IGNORECASE)

    # Collapse repeated whitespace and blank lines
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def run_pipeline(file_path: Path) -> list[Path]:
    """Runs the transcript processing pipeline on a single file."""
    print(f"[process_transcripts] Ingesting: {file_path.name}")

    if file_path.suffix.lower() == ".pdf":
        print(f"[process_transcripts] Extracting PDF layout first...")
        from extract import extract_pdf
        stem = slugify(file_path.stem)
        text = extract_pdf(file_path, stem)
    else:
        text = file_path.read_text(errors="ignore")

    # Clean transcript before LLM segmentation: remove filler words + diarization markers
    text = clean_transcript(text)

    print(f"[process_transcripts] Cleaning and segmenting into pedagogical concepts...")
    concepts = process_text_into_concepts(text)
    return save_concepts(concepts, file_path.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Structure transcript text files into concept databases.")
    parser.add_argument("file", help="Path to transcript text or PDF file.")
    args = parser.parse_args()
    
    src = Path(args.file)
    if not src.exists():
        print(f"Error: file not found: {src}")
        exit(1)
        
    run_pipeline(src)
