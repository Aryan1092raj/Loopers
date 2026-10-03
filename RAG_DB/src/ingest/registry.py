"""Phase 1 — Ingest.

Scan raw/, record each file in metadata/registry.json with a content hash.
Idempotent: unchanged files are skipped, changed files are re-flagged for
downstream reprocessing.
"""
import hashlib
import json
from pathlib import Path

from config import RAW, REGISTRY, METADATA, ROOT, IMAGES, classify, subject_for_file


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return {}


def save_registry(reg: dict) -> None:
    METADATA.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def run() -> dict:
    reg = load_registry()
    new, changed, unchanged = 0, 0, 0

    files = [
        p for p in sorted(RAW.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    ]

    current_files = {str(p.relative_to(RAW)) for p in files}
    pruned = 0
    for rel in list(reg.keys()):
        if rel not in current_files:
            # Clean up generated files if they exist
            entry = reg[rel]
            if "markdown" in entry:
                md_path = ROOT / entry["markdown"]
                if md_path.exists():
                    try:
                        md_path.unlink()
                    except Exception as e:
                        print(f"[ingest] failed to delete {md_path}: {e}")
            
            # Clean up associated images
            stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(rel).stem)
            for img_path in IMAGES.glob(f"{stem}_p*"):
                try:
                    img_path.unlink()
                except Exception as e:
                    print(f"[ingest] failed to delete {img_path}: {e}")
            for img_path in IMAGES.glob(f"{stem}.*"):
                try:
                    img_path.unlink()
                except Exception as e:
                    print(f"[ingest] failed to delete {img_path}: {e}")

            del reg[rel]
            pruned += 1

    for path in files:
        rel = str(path.relative_to(RAW))
        digest = sha256(path)
        ftype = classify(path)

        entry = reg.get(rel)
        subject = subject_for_file(path)
        if entry is None:
            reg[rel] = {"sha256": digest, "type": ftype, "status": "ingested", "subject": subject}
            new += 1
        elif entry["sha256"] != digest:
            entry.update(sha256=digest, type=ftype, status="ingested", subject=subject)
            changed += 1
        else:
            # Backfill subject if missing from old registry entries
            if "subject" not in entry:
                entry["subject"] = subject
            unchanged += 1

    save_registry(reg)
    print(
        f"[ingest] {len(files)} files scanned | "
        f"{new} new, {changed} changed, {unchanged} unchanged"
    )
    if pruned > 0:
        print(f"[ingest] pruned {pruned} deleted files from registry")
    if new == 0 and changed == 0 and files and pruned == 0:
        print("[ingest] nothing to do — registry up to date")
    return reg


if __name__ == "__main__":
    run()
