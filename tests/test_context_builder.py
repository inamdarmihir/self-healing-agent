"""Tests for agent/nodes/context_builder.py."""

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.nodes.context_builder import (
    _count_tokens,
    _find_module_file,
    _parse_imports_treesitter,
    _read_file_safe,
    build_context,
)
from agent.state import AgentState

# Absolute path to the sample repo fixture
_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def _base_state(**overrides: object) -> AgentState:
    """Minimal valid AgentState for context_builder tests."""
    state: AgentState = {
        "task_id": "test-001",
        "repo_path": str(_FIXTURE_REPO),
        "failing_tests": ["test_main.py"],
        "issue_description": "add() returns wrong value",
        "relevant_files": [],
        "file_contents": {},
        "current_patch": None,
        "patch_history": [],
        "reflection_critique": None,
        "reflection_approved": False,
        "test_results": None,
        "tests_passed": False,
        "iteration": 0,
        "max_iterations": 5,
        "token_usage": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "llm_calls": 0,
        "status": "running",
        "pr_url": None,
        "postmortem": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class TestCountTokens:
    def test_empty_string(self) -> None:
        assert _count_tokens("") == 0

    def test_nonempty_string(self) -> None:
        count = _count_tokens("hello world")
        assert count > 0

    def test_proportional(self) -> None:
        short = _count_tokens("a")
        long = _count_tokens("a " * 100)
        assert long > short


class TestReadFileSafe:
    def test_reads_utf8_file(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.py"
        f.write_text("x = 1\n", encoding="utf-8")
        content = _read_file_safe(f)
        assert content == "x = 1\n"

    def test_returns_none_for_large_file(self, tmp_path: Path) -> None:
        f = tmp_path / "big.py"
        f.write_bytes(b"x" * (500 * 1024 + 1))
        assert _read_file_safe(f) is None

    def test_returns_none_for_binary(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.bin"
        f.write_bytes(bytes(range(256)))
        # Binary files raise UnicodeDecodeError → should return None
        assert _read_file_safe(f) is None


class TestParseImportsTreesitter:
    # Skip entire class when tree-sitter-python is not installed.
    # The parser falls back to [] gracefully at runtime; these tests
    # verify correct behavior only when the dependency is present.
    pytest.importorskip("tree_sitter_python", reason="tree-sitter-python not installed")
    def test_simple_import(self) -> None:
        source = b"import os\n"
        modules = _parse_imports_treesitter(source)
        assert "os" in modules

    def test_from_import(self) -> None:
        source = b"from pathlib import Path\n"
        modules = _parse_imports_treesitter(source)
        assert "pathlib" in modules

    def test_no_imports(self) -> None:
        source = b"x = 1\n"
        modules = _parse_imports_treesitter(source)
        assert modules == []

    def test_dotted_import_takes_root(self) -> None:
        source = b"import os.path\n"
        modules = _parse_imports_treesitter(source)
        assert "os" in modules

    def test_deduplicates(self) -> None:
        source = b"import os\nimport os\n"
        modules = _parse_imports_treesitter(source)
        assert modules.count("os") == 1


class TestFindModuleFile:
    def test_finds_direct_py_file(self) -> None:
        result = _find_module_file("main", _FIXTURE_REPO)
        assert result is not None
        assert result.name == "main.py"

    def test_returns_none_for_stdlib(self) -> None:
        result = _find_module_file("os", _FIXTURE_REPO)
        # os.py doesn't exist in the fixture repo
        assert result is None

    def test_returns_none_for_unknown(self) -> None:
        result = _find_module_file("nonexistent_module_xyz", _FIXTURE_REPO)
        assert result is None


class TestBuildContext:
    def test_includes_test_file(self) -> None:
        state = _base_state()
        result = build_context(state)
        assert "test_main.py" in result["relevant_files"]
        assert "test_main.py" in result["file_contents"]

    def test_follows_imports(self) -> None:
        state = _base_state()
        result = build_context(state)
        # test_main.py imports 'main', so main.py should be included
        assert any("main.py" in f for f in result["relevant_files"])

    def test_missing_test_file_is_skipped(self) -> None:
        state = _base_state(failing_tests=["nonexistent_test.py"])
        result = build_context(state)
        # Should not crash; just skip the missing file
        assert result["relevant_files"] == []

    def test_returns_dict_with_correct_keys(self) -> None:
        state = _base_state()
        result = build_context(state)
        assert set(result.keys()) == {"relevant_files", "file_contents"}

    def test_file_contents_are_strings(self) -> None:
        state = _base_state()
        result = build_context(state)
        for content in result["file_contents"].values():
            assert isinstance(content, str)
