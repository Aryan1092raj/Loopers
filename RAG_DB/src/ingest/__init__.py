# ingest package
# The flat ingest.py moved to ingest/registry.py (the package shadowed it); re-export it so
# `import ingest; ingest.run()` and `from ingest import load_registry` keep working.
from ingest.registry import load_registry, run, save_registry, sha256  # noqa: F401
