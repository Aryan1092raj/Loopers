"""MinerU parser wrapper for STEM PDFs (Phase 3).

MinerU (magic_pdf) uses PDF-Extract-Kit + UniMERNet for >90% BLEU formula
recognition.  Outputs clean Markdown with LaTeX-formatted equations.

Install:
    pip install magic-pdf
    magic-pdf --install-models  (downloads UniMERNet + layout models, ~2-4 GB)

Falls back gracefully if magic_pdf is not installed.
"""
from pathlib import Path


def parse(pdf_path: str | Path, output_dir: str | Path) -> str | None:
    """Parse a STEM PDF with MinerU and return the extracted Markdown string.

    Writes intermediate files to output_dir/mineru/<stem>/.
    Returns None if MinerU is not installed or parsing fails.

    Args:
        pdf_path:   Path to the source PDF.
        output_dir: Directory to write MinerU's intermediate outputs.
    """
    try:
        from magic_pdf.data.data_reader_writer import FileBasedDataWriter
        from magic_pdf.data.dataset import PymuDocDataset
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze
        from magic_pdf.config.enums import SupportedPdfParseMethod
    except ImportError:
        return None  # MinerU not installed — caller will fall back to PP-StructureV3

    try:
        pdf_path = Path(pdf_path)
        output_dir = Path(output_dir) / "mineru" / pdf_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        image_dir = output_dir / "images"
        image_dir.mkdir(exist_ok=True)

        writer = FileBasedDataWriter(str(output_dir))
        image_writer = FileBasedDataWriter(str(image_dir))

        pdf_bytes = pdf_path.read_bytes()
        ds = PymuDocDataset(pdf_bytes)

        # Infer parse method (auto-detect text vs OCR)
        if ds.classify() == SupportedPdfParseMethod.OCR:
            from magic_pdf.pipe.OCRPipe import OCRPipe
            pipe = OCRPipe(ds, [], image_writer)
        else:
            from magic_pdf.pipe.UNIPipe import UNIPipe
            pipe = UNIPipe(ds, {"_pdf_type": "", "model_list": []}, image_writer)

        pipe.pipe_classify()
        pipe.pipe_analyze()
        pipe.pipe_parse()
        pipe.pipe_mk_markdown(writer)

        md_file = output_dir / f"{pdf_path.stem}.md"
        if md_file.exists():
            return md_file.read_text(encoding="utf-8")
        return None

    except Exception as exc:
        print(f"[parsers/mineru] Warning: MinerU parsing failed for {pdf_path}: {exc}")
        return None
