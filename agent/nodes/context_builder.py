"""Parse the repository to extract only the files relevant to the failing tests.

Uses tree-sitter to understand import graphs, not just filename matching.
Caps total context at 8,000 tokens (truncate with a comment if exceeded).
"""

import logging
from pathlib import Path

import tiktoken

from agent.state import AgentState

logger = logging.getLogger(__name__)

# Token budget for all file contents combined
_TOKEN_CAP = 8_000
_MAX_FILE_BYTES = 500 * 1024  # 500 KB

# Initialise tokeniser once — cl100k_base approximates all modern Claude/GPT models
_ENC = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    """Return approximate token count for a string."""
    return len(_ENC.encode(text))


def _parse_imports_treesitter(source: bytes) -> list[str]:
    """Extract top-level module names from a Python source file via tree-sitter.

    Returns a list of module root names, e.g. 'os', 'agent', 'requests'.
    Falls back to an empty list if tree-sitter is unavailable.
    """
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        py_lang = Language(tspython.language())
        parser = Parser(py_lang)
        tree = parser.parse(source)

        modules: list[str] = []

        def traverse(node: object) -> None:
            # tree_sitter Node — use duck typing to avoid strict import of Node type
            node_type: str = getattr(node, "type", "")
            children: list[object] = getattr(node, "children", [])

            if node_type == "import_statement":
                # import os, import os.path → dotted_name children
                for child in children:
                    if getattr(child, "type", "") in ("dotted_name", "aliased_import"):
                        # Take first dotted component as the root module
                        text: bytes = getattr(child, "text", b"")
                        root = text.decode("utf-8", errors="replace").split(".")[0]
                        if root:
                            modules.append(root)
                        break  # only first name in `import a, b`

            elif node_type == "import_from_statement":
                # from os.path import join → module_name is the first dotted_name
                for child in children:
                    if getattr(child, "type", "") == "dotted_name":
                        text = getattr(child, "text", b"")
                        root = text.decode("utf-8", errors="replace").split(".")[0]
                        if root:
                            modules.append(root)
                        break

            for child in children:
                traverse(child)

        traverse(tree.root_node)
        return list(dict.fromkeys(modules))  # deduplicate, preserve order

    except ImportError:
        logger.warning("tree-sitter-python not available; falling back to empty import list")
        return []


def _find_module_file(module_root: str, repo_path: Path) -> Path | None:
    """Locate the source file for a module root name within the repo.

    Checks <module_root>.py and <module_root>/__init__.py at repo root and
    one level of subdirectories (direct-dependency only).
    """
    candidates = [
        repo_path / f"{module_root}.py",
        repo_path / module_root / "__init__.py",
    ]
    for subdir in repo_path.iterdir():
        if subdir.is_dir() and not subdir.name.startswith("."):
            candidates.append(subdir / f"{module_root}.py")
            candidates.append(subdir / module_root / "__init__.py")

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _read_file_safe(path: Path) -> str | None:
    """Read a file, returning None for binary or oversized files."""
    if path.stat().st_size > _MAX_FILE_BYTES:
        logger.info("Skipping %s — exceeds 500 KB size limit", path)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.info("Skipping %s — binary or non-UTF-8 content", path)
        return None


def build_context(state: AgentState) -> dict:  # type: ignore[type-arg]
    """Parse the repository and extract files relevant to the failing tests.

    Node function: receives full AgentState, returns partial update dict.

    Returns:
        {"relevant_files": [...], "file_contents": {...}}
    """
    repo_path = Path(state["repo_path"])
    failing_tests = state["failing_tests"]

    relevant_files: list[str] = []
    file_contents: dict[str, str] = {}
    tokens_used = 0

    included: list[str] = []
    excluded: list[str] = []

    def _add_file(path: Path, reason: str) -> bool:
        """Add a file to the context if budget allows. Returns True on success."""
        nonlocal tokens_used
        rel = str(path.relative_to(repo_path))

        if rel in file_contents:
            return True  # already added

        content = _read_file_safe(path)
        if content is None:
            excluded.append(f"{rel} (unreadable)")
            return False

        tokens = _count_tokens(content)
        if tokens_used + tokens > _TOKEN_CAP:
            # Truncate to fit remaining budget
            remaining = _TOKEN_CAP - tokens_used
            if remaining <= 0:
                excluded.append(f"{rel} (token budget exhausted)")
                return False
            # Encode → slice → decode to avoid splitting multi-byte chars
            encoded = _ENC.encode(content)[:remaining]
            content = _ENC.decode(encoded) + f"\n# [TRUNCATED — {tokens - remaining} tokens omitted]"
            tokens = remaining
            logger.info("Truncated %s to fit token budget (%d tokens omitted)", rel, tokens - remaining)

        relevant_files.append(rel)
        file_contents[rel] = content
        tokens_used += tokens
        included.append(f"{rel} ({reason})")
        return True

    # Step 1: always include the failing test files
    for test_path_str in failing_tests:
        test_path = Path(test_path_str)
        if not test_path.is_absolute():
            test_path = repo_path / test_path_str

        if not test_path.exists():
            logger.warning("Failing test file not found: %s", test_path)
            excluded.append(f"{test_path_str} (not found)")
            continue

        _add_file(test_path, "failing test")

    # Step 2: parse imports from test files and follow one level deep
    for test_path_str in failing_tests:
        test_path = Path(test_path_str)
        if not test_path.is_absolute():
            test_path = repo_path / test_path_str
        if not test_path.exists():
            continue

        try:
            source_bytes = test_path.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s for import analysis: %s", test_path, exc)
            continue

        imports = _parse_imports_treesitter(source_bytes)
        logger.debug("Imports found in %s: %s", test_path.name, imports)

        for module_root in imports:
            if tokens_used >= _TOKEN_CAP:
                logger.info("Token budget exhausted; stopping import resolution")
                break

            module_file = _find_module_file(module_root, repo_path)
            if module_file is None:
                logger.debug("Module '%s' not found in repo (stdlib/third-party)", module_root)
                continue

            _add_file(module_file, f"imported by {test_path.name}")

    # Step 3: semantic search via Qdrant
    # Augments the import-graph results with files that are semantically
    # relevant to the issue description and failing tests but not directly
    # imported.  Degrades gracefully if qdrant-client is not installed.
    try:
        from agent.nodes.qdrant_store import search_relevant_files

        query_parts: list[str] = []
        issue_desc: str = state.get("issue_description", "") or ""  # type: ignore[assignment]
        if issue_desc:
            query_parts.append(issue_desc)
        query_parts.extend(failing_tests)
        semantic_query = "\n".join(query_parts)

        semantic_hits = search_relevant_files(
            query=semantic_query,
            repo_path=str(repo_path),
            top_k=5,
        )
        for rel_path in semantic_hits:
            if tokens_used >= _TOKEN_CAP:
                logger.info("Token budget exhausted; stopping semantic search")
                break
            candidate = repo_path / rel_path
            if candidate.exists():
                _add_file(candidate, "semantic search (Qdrant)")
    except ImportError:
        logger.info(
            "qdrant-client not installed; skipping semantic search "
            "(install with: pip install 'qdrant-client[fastembed]')"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant semantic search failed: %s", exc)

    logger.info(
        "Context built — %d files, %d tokens | included: %s | excluded: %s",
        len(relevant_files),
        tokens_used,
        included,
        excluded,
    )

    return {
        "relevant_files": relevant_files,
        "file_contents": file_contents,
    }
