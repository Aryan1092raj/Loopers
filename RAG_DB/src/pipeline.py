"""Multimodal RAG pipeline CLI.

    python src/pipeline.py ingest
    python src/pipeline.py extract
    python src/pipeline.py label
    python src/pipeline.py embed              # incremental (default)
    python src/pipeline.py embed --full       # force full rebuild
    python src/pipeline.py generate "explain how neural networks are trained"
    python src/pipeline.py generate "gaussian elimination" --subject math
    python src/pipeline.py all "topic"        # run every phase in order
    python src/pipeline.py all "topic" --full # full rebuild + generate

Phases 1, 2, 4 are local.
Phase 3 (label) uses Gemini/NVIDIA NIM. Phase 5 (generate) uses Groq (4 keys) — needs GROQ_API_KEY in .env.
Eval --judge calls Groq — needs GROQ_API_KEY in .env.
"""
import argparse

import ingest
import extract
import label
import embed
import generate
import eval as evaluate
import corpus_downloader


def main() -> None:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ingest", help="Phase 1 — register raw files")
    sub.add_parser("extract", help="Phase 2 — raw -> markdown + figures (parallel)")
    sub.add_parser("label", help="Phase 3 — caption/classify figures via OpenRouter (parallel)")

    emb = sub.add_parser("embed", help="Phase 4 — chunk + embed into Qdrant (BGE-M3 GPU)")
    emb.add_argument("--full", action="store_true",
                     help="Force full rebuild (wipes collection, re-embeds everything)")
    emb.add_argument("--reset-collection", action="store_true",
                     help="Drop and recreate Qdrant collection (migrates to hybrid schema) then re-embed all")

    med = sub.add_parser("media", help="Phase 4a — transcribe + embed one video/audio source (clips embed with their speech)")
    med.add_argument("source_id")
    med.add_argument("--job-id", default="cli")

    sub.add_parser("concepts", help="Phase 4b — concept graph + tag every unit (needs an LLM key in .env)")
    sub.add_parser("seed-items", help="Phase 4c — pre-generate item bank to eliminate quiz latency (TRD §6.5 / Issue #19)")

    g = sub.add_parser("generate", help="Phase 5 — RAG topic -> .pdf via OpenRouter and LaTeX")
    g.add_argument("query", nargs="+", help="topic query")
    g.add_argument("--subject", default=None,
                   help="Scope retrieval to a subject tag (e.g. math, physics)")
    g.add_argument("--grade-level", default=None, choices=["basic", "intermediate", "advanced"],
                   help="Learner grade level for CLAF pedagogical adaptation")

    n = sub.add_parser("notes", help="Generate study notes PDF from topic")
    n.add_argument("query", nargs="+", help="topic query")
    n.add_argument("--subject", default=None, help="Subject hint (auto-detected if omitted)")

    asn = sub.add_parser("assignment", help="Generate assignment PDF (student + teacher) from topic")
    asn.add_argument("query", nargs="+", help="topic query")
    asn.add_argument("--subject", default=None, help="Subject hint (auto-detected if omitted)")
    asn.add_argument("--type", default=None, choices=["mcq", "short", "long", "math", "mixed"],
                     help="Question type (mcq, short, long, math, mixed)")
    asn.add_argument("--count", type=int, default=None,
                     help="Number of questions to generate")

    e = sub.add_parser("eval", help="retrieval hit@k over eval_questions.json")
    e.add_argument("--k", type=int, default=5)
    e.add_argument("--judge", action="store_true", help="also Groq-score answers (API)")
    e.add_argument("--figures", action="store_true", help="run metadata/figure_questions.json (needs labelled figures)")

    sub.add_parser("eval-gen", help="Auto-generate relevant evaluation questions from processed content")

    dl = sub.add_parser("download", help="Phase 0 — download open-access textbooks (OpenStax / NCERT)")
    dl.add_argument("--source", choices=["openstax", "ncert", "list"], required=True,
                    help="Source to download from (or 'list' to show available subjects)")
    dl.add_argument("--subjects", nargs="+", default=["all"],
                    help="Subject slugs to download (use 'all' for everything)")
    dl.add_argument("--classes", nargs="+", type=int, default=[10, 11, 12],
                    help="Class numbers for NCERT downloads (default: 10 11 12)")
    dl.add_argument("--dest", default=None,
                    help="Destination directory (default: raw/)")

    asb = sub.add_parser("add-subject", help="Bulk workflow: download, ingest, extract, label, embed a subject")
    asb.add_argument("--source", choices=["openstax", "ncert"], required=True)
    asb.add_argument("--subjects", nargs="+", required=True)
    asb.add_argument("--target-subject", required=True, help="Canonical subject name to target")
    asb.add_argument("--classes", nargs="+", type=int, default=[10, 11, 12])

    a_cmd = sub.add_parser("all", help="run phases 1-5 in order")
    a_cmd.add_argument("query", nargs="+", help="topic query for generation")
    a_cmd.add_argument("--full", action="store_true", help="Force full embed rebuild")
    a_cmd.add_argument("--subject", default=None)
    a_cmd.add_argument("--grade-level", default=None, choices=["basic", "intermediate", "advanced"],
                       help="Learner grade level for CLAF pedagogical adaptation")

    args = parser.parse_args()

    if args.cmd == "ingest":
        ingest.run()
    elif args.cmd == "extract":
        extract.run()
    elif args.cmd == "label":
        label.run()
    elif args.cmd == "embed":
        embed.run(force_full=args.full or args.reset_collection)
    elif args.cmd == "media":
        import sys
        from config import ROOT
        from core.db import get_connection
        from ingest.video_worker import process_video

        conn = get_connection()
        row = conn.execute("SELECT media_path FROM sources WHERE source_id = ?", (args.source_id,)).fetchone()
        conn.close()
        if not row or not row[0]:
            sys.exit(f"[media] no media_path for source {args.source_id}")
        process_video(args.job_id, args.source_id, row[0] if row[0].startswith(("http://", "https://")) else str(ROOT / row[0]))
        conn = get_connection()
        job = conn.execute("SELECT stage, error FROM jobs WHERE job_id = ?", (args.job_id,)).fetchone()
        conn.close()
        if job and job[0] == "failed":  # process_video records failures instead of raising
            sys.exit(f"[media] failed: {job[1]}")
    elif args.cmd == "concepts":
        from concepts import build
        from tutor.answer import NoProvider

        try:
            build.run()
        except NoProvider as e:  # not a failure of the upload: units are searchable, only concept tags are missing
            print(f"[concepts] skipped, no LLM provider answered: {e}")
    elif args.cmd == "seed-items":
        from assess.seed import seed_item_bank
        seed_item_bank()
    elif args.cmd == "generate":
        query_str = " ".join(args.query)
        # generate.run() now produces student notes + assignment only (no teacher pptx).
        generate.run(query_str, subject=args.subject, grade_level=args.grade_level)
    elif args.cmd == "notes":
        import notes
        notes.run(" ".join(args.query), subject=args.subject)
    elif args.cmd == "assignment":
        import assignment
        out = assignment.run(
            " ".join(args.query),
            subject=args.subject,
            assignment_type=args.type,
            question_count=args.count
        )
        print(f"[assignment] student: {out[0]}")
        print(f"[assignment] teacher: {out[1]}")
    elif args.cmd == "eval":
        evaluate.run(k=args.k, judge=args.judge, figures=args.figures)
    elif args.cmd == "eval-gen":
        evaluate.generate_eval_questions()
    elif args.cmd == "download":
        from config import RAW
        from pathlib import Path
        dest = Path(args.dest) if args.dest else RAW
        if args.source == "list":
            corpus_downloader.list_available()
        elif args.source == "openstax":
            corpus_downloader.download_openstax(args.subjects, dest)
        elif args.source == "ncert":
            corpus_downloader.download_ncert(args.classes, args.subjects, dest)
    elif args.cmd == "add-subject":
        from config import subject_raw_dir
        dest = subject_raw_dir(args.target_subject)
        if args.source == "openstax":
            corpus_downloader.download_openstax(args.subjects, dest)
        elif args.source == "ncert":
            corpus_downloader.download_ncert(args.classes, args.subjects, dest)
        ingest.run()
        extract.run()
        label.run()
        embed.run()
    elif args.cmd == "all":
        ingest.run()
        extract.run()
        label.run()
        embed.run(force_full=args.full)
        query_str = " ".join(args.query)
        # generate.run() emits student notes + assignment; no teacher pptx.
        generate.run(query_str, subject=args.subject, grade_level=args.grade_level)


if __name__ == "__main__":
    import logging

    # Workers report failures through `logging`; without a handler they never reach the job log.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    main()
