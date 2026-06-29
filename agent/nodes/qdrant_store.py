"""Qdrant-backed semantic search over repository source files.

Integration point: context_builder calls index_repository() once per task run,
then search_relevant_files() to surface semantically relevant files beyond
what the tree-sitter import-graph analysis captures.

Uses qdrant-client's bundled fastembed integration (BAAI/bge-small-en-v1.5,
384-dim) so no separate API key is needed for embeddings.  The model weights
(~120 MB) are downloaded on first use and cached in ~/.cache/fastembed.

Config via env vars:
  QDRANT_URL      — Qdrant endpoint (default: in-memory, no persistence)
  QDRANT_API_KEY  — API key for Qdrant Cloud (optional)
"""

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_CHUNK_CHARS = 2_000   # chars to embed per file (first N chars)
_COLLECTION_PREFIX = "sha_"

# ---------------------------------------------------------------------------
# Module-level singletons — survive across LangGraph node calls in the same
# process so in-memory collections are not lost between graph nodes.
# ---------------------------------------------------------------------------
_client = None  # type: ignore[assignment]  # QdrantClient | None
_indexed: set[str] = set()  # collection names already indexed this run


def _get_client():  # type: ignore[return]
    """Return (and lazily create) the module-level Qdrant client.

    Raises ImportError if qdrant-client is not installed.
    """
    global _client  # noqa: PLW0603
    if _client is not None:
        return _client

    try:
        from qdrant_client import QdrantClient  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "qdrant-client is required for semantic search. "
            "Install it with: pip install 'qdrant-client[fastembed]'"
        ) from exc

    url = os.getenv("QDRANT_URL", "").strip()
    api_key = os.getenv("QDRANT_API_KEY", "").strip() or None

    if url:
        _client = QdrantClient(url=url, api_key=api_key)
        logger.info("Qdrant: connected to %s", url)
    else:
        _client = QdrantClient(":memory:")
        logger.info(
            "Qdrant: using in-memory store "
            "(set QDRANT_URL to use a persistent instance)"
        )
    return _client


def _collection_name(repo_path: str) -> str:
    """Derive a stable, short collection name from the repo path."""
    h = hashlib.sha1(repo_path.encode()).hexdigest()[:16]
    return f"{_COLLECTION_PREFIX}{h}"


def index_repository(repo_path: str) -> str:
    """Index all .py files in repo_path into a Qdrant collection.

    Idempotent within a single process: skips re-indexing if the collection
    was already built during this run.

    Returns:
        The Qdrant collection name used.

    Raises:
        ImportError: if qdrant-client[fastembed] is not installed.
    """
    cname = _collection_name(repo_path)

    if cname in _indexed:
        logger.debug("Qdrant: collection '%s' already indexed — skipping", cname)
        return cname

    client = _get_client()

    # Drop stale collection from a previous run (ignore errors if absent)
    try:
        client.delete_collection(cname)
    except Exception:  # noqa: BLE001
        pass

    py_files = sorted(Path(repo_path).rglob("*.py"))
    documents: list[str] = []
    metadata: list[dict[str, str]] = []

    for fpath in py_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("Qdrant: skipping %s (%s)", fpath, exc)
            continue
        rel = str(fpath.relative_to(repo_path))
        documents.append(text[:_MAX_CHUNK_CHARS])
        metadata.append({"file_path": rel})

    if not documents:
        logger.warning("Qdrant: no Python files found in '%s'", repo_path)
        _indexed.add(cname)
        return cname

    # client.add() uses the bundled fastembed model to embed documents and
    # upserts them into the collection (creating it on first call).
    client.add(
        collection_name=cname,
        documents=documents,
        metadata=metadata,  # type: ignore[arg-type]
    )
    _indexed.add(cname)
    logger.info(
        "Qdrant: indexed %d files into collection '%s'", len(documents), cname
    )
    return cname


def search_relevant_files(
    query: str,
    repo_path: str,
    top_k: int = 5,
) -> list[str]:
    """Return up to top_k relative file paths most semantically similar to query.

    Calls index_repository() automatically on first use.
    Returns an empty list on any failure so callers degrade gracefully.
    """
    if not query.strip():
        return []
    try:
        cname = index_repository(repo_path)
        client = _get_client()
        hits = client.query(
            collection_name=cname,
            query_text=query,
            limit=top_k,
        )
        return [
            h.metadata["file_path"]
            for h in hits
            if h.metadata and h.metadata.get("file_path")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant: search failed — %s", exc)
        return []
