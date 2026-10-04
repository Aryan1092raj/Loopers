"""Phase 7 — Automated corpus acquisition.

Downloads open-access textbooks from:
  - OpenStax (CC-BY 4.0 peer-reviewed textbooks from Rice University)
  - NCERT   (National Council of Educational Research and Training, India)

Usage (CLI):
    python pipeline.py download --source openstax --subjects physics chemistry
    python pipeline.py download --source ncert --classes 10 11 12 --subjects physics

Usage (Python):
    from corpus_downloader import download_openstax, download_ncert
    download_openstax(["physics", "chemistry"], dest_dir=Path("raw"))
"""
import json
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# OpenStax catalog — maps subject slug to PDF download URL
# ---------------------------------------------------------------------------

OPENSTAX_CATALOG: dict[str, str] = {
    "physics-vol1": "https://assets.openstax.org/oscms-prodcms/media/documents/UniversityPhysicsVolume1-WEB_5yOHoGn.pdf",
    "physics-vol2": "https://assets.openstax.org/oscms-prodcms/media/documents/UniversityPhysicsVolume2-WEB_27bgB7A.pdf",
    "physics-vol3": "https://assets.openstax.org/oscms-prodcms/media/documents/UniversityPhysicsVolume3-WEB_eCWMrX9.pdf",
    "chemistry":    "https://assets.openstax.org/oscms-prodcms/media/documents/Chemistry2e-WEB_7Wbidtx.pdf",
    "biology":      "https://assets.openstax.org/oscms-prodcms/media/documents/Biology2e-WEB_jPEbvhN.pdf",
    "calculus-vol1":"https://assets.openstax.org/oscms-prodcms/media/documents/calculus-volume-1_-_WEB.pdf",
    "calculus-vol2":"https://assets.openstax.org/oscms-prodcms/media/documents/CalculusVolume2-WEB_3gkPxuT.pdf",
    "statistics":   "https://assets.openstax.org/oscms-prodcms/media/documents/IntroductoryStatistics-WEB_YkxdPZ0.pdf",
    "economics":    "https://assets.openstax.org/oscms-prodcms/media/documents/Principles_of_Economics_3e-WEB_xr7RfNi.pdf",
    "us-history":   "https://assets.openstax.org/oscms-prodcms/media/documents/USHistory-WEB_d7fJE9e.pdf",
    "world-history":"https://assets.openstax.org/oscms-prodcms/media/documents/WorldHistory-WEB_2EKKSmq.pdf",
    "sociology":    "https://assets.openstax.org/oscms-prodcms/media/documents/IntroductiontoSociology3e-WEB_B4GHpMX.pdf",
    "psychology":   "https://assets.openstax.org/oscms-prodcms/media/documents/Psychology2e-WEB_OzEqczq.pdf",
    "anatomy":      "https://assets.openstax.org/oscms-prodcms/media/documents/AnatomyandPhysiology2e-WEB_Ah8WRkB.pdf",
}

# ---------------------------------------------------------------------------
# NCERT base URL pattern (class 6-12, Hindi + English)
# ---------------------------------------------------------------------------

NCERT_BASE = "https://ncert.nic.in/textbook/pdf/{subject}{cls}{part}.pdf"

# Maps subject slug to NCERT subject code prefix
NCERT_SUBJECTS: dict[str, str] = {
    "physics":     "leph",
    "chemistry":   "lech",
    "mathematics": "lemh",
    "biology":     "lebo",
    "history":     "lehs",
    "geography":   "legs",
    "economics":   "leec",
    "english":     "leel",
    "hindi":       "lehd",
}


def _download_file(url: str, dest: Path, retries: int = 3) -> bool:
    """Download url to dest. Returns True on success."""
    for attempt in range(retries):
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
                if r.status_code != 200:
                    print(f"[downloader] HTTP {r.status_code} for {url}")
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            return True
        except Exception as exc:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"[downloader] Retry {attempt + 1}/{retries} for {url} ({exc}), waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"[downloader] Failed to download {url}: {exc}")
    return False


def download_openstax(
    subjects: list[str],
    dest_dir: Path | str,
) -> dict[str, bool]:
    """Download OpenStax PDFs for the given subject slugs.

    subject slugs: keys of OPENSTAX_CATALOG (e.g. "physics-vol1", "chemistry")
    Use "all" to download the entire catalog.

    Returns dict mapping subject slug -> success bool.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if "all" in subjects:
        subjects = list(OPENSTAX_CATALOG.keys())

    results = {}
    for slug in subjects:
        url = OPENSTAX_CATALOG.get(slug)
        if not url:
            print(f"[downloader] Unknown OpenStax subject: '{slug}'. "
                  f"Available: {', '.join(OPENSTAX_CATALOG.keys())}")
            results[slug] = False
            continue
        dest = dest_dir / f"openstax_{slug}.pdf"
        if dest.exists():
            print(f"[downloader] Already exists: {dest.name}")
            results[slug] = True
            continue
        print(f"[downloader] Downloading OpenStax '{slug}'...")
        ok = _download_file(url, dest)
        results[slug] = ok
        if ok:
            print(f"[downloader]   -> {dest.name}")
    return results


def download_ncert(
    classes: list[int],
    subjects: list[str],
    dest_dir: Path | str,
    max_parts: int = 3,
) -> dict[str, bool]:
    """Download NCERT PDFs for given classes and subjects.

    classes: list of class numbers, e.g. [10, 11, 12]
    subjects: list of subject slugs from NCERT_SUBJECTS keys
    max_parts: max part numbers to try per class+subject combo (NCERT splits books)

    Returns dict mapping "subject_class_partN" -> success bool.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for subj in subjects:
        code = NCERT_SUBJECTS.get(subj)
        if not code:
            print(f"[downloader] Unknown NCERT subject: '{subj}'. "
                  f"Available: {', '.join(NCERT_SUBJECTS.keys())}")
            continue
        for cls in classes:
            for part in range(1, max_parts + 1):
                key = f"{subj}_class{cls}_part{part}"
                url = NCERT_BASE.format(subject=code, cls=cls, part=part)
                dest = dest_dir / f"ncert_{subj}_class{cls}_part{part}.pdf"
                if dest.exists():
                    print(f"[downloader] Already exists: {dest.name}")
                    results[key] = True
                    continue
                print(f"[downloader] Downloading NCERT {subj} class {cls} part {part}...")
                ok = _download_file(url, dest)
                results[key] = ok
                if ok:
                    print(f"[downloader]   -> {dest.name}")
                else:
                    break  # no more parts for this class+subject combo
    return results


def list_available() -> None:
    """Print available subjects for both OpenStax and NCERT."""
    print("=== OpenStax subjects ===")
    for slug, url in OPENSTAX_CATALOG.items():
        print(f"  {slug}")
    print("\n=== NCERT subjects ===")
    for slug in NCERT_SUBJECTS:
        print(f"  {slug}  (classes 6-12)")


if __name__ == "__main__":
    import argparse
    from config import RAW

    ap = argparse.ArgumentParser(description="Download open-access textbooks")
    ap.add_argument("--source", choices=["openstax", "ncert", "list"], required=True,
                    help="Download source or 'list' to show available subjects")
    ap.add_argument("--subjects", nargs="+", default=["all"],
                    help="Subject slugs (use 'all' for OpenStax to download everything)")
    ap.add_argument("--classes", nargs="+", type=int, default=[10, 11, 12],
                    help="Class numbers for NCERT (default: 10 11 12)")
    ap.add_argument("--dest", default=None,
                    help="Destination directory (default: raw/)")
    a = ap.parse_args()

    dest = Path(a.dest) if a.dest else RAW

    if a.source == "list":
        list_available()
    elif a.source == "openstax":
        results = download_openstax(a.subjects, dest)
        ok = sum(1 for v in results.values() if v)
        print(f"\n[downloader] Done: {ok}/{len(results)} downloaded to {dest}/")
    elif a.source == "ncert":
        results = download_ncert(a.classes, a.subjects, dest)
        ok = sum(1 for v in results.values() if v)
        print(f"\n[downloader] Done: {ok}/{len(results)} files downloaded to {dest}/")
