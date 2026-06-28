"""Tests for agent/nodes/patch_generator.py."""

from unittest.mock import MagicMock, patch

import pytest

from agent.nodes.patch_generator import (
    _compute_cost,
    _format_file_contents,
    _format_patch_history,
    generate_patch,
)
from agent.state import AgentState


def _base_state(**overrides: object) -> AgentState:
    state: AgentState = {
        "task_id": "test-patch-001",
        "repo_path": "/tmp/repo",
        "failing_tests": ["test_main.py"],
        "issue_description": "add() returns wrong value",
        "relevant_files": ["main.py"],
        "file_contents": {"main.py": "def add(a, b):\n    return a - b\n"},
        "current_patch": None,
        "patch_history": [],
        "reflection_critique": None,
        "reflection_approved": False,
        "test_results": "FAILED test_main.py::test_add",
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


class TestComputeCost:
    def test_known_model(self) -> None:
        cost = _compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(3.00 + 15.00)

    def test_unknown_model_fallback(self) -> None:
        # Should fall back to claude-sonnet-4-6 pricing
        cost = _compute_cost("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(3.00)

    def test_zero_tokens(self) -> None:
        assert _compute_cost("claude-sonnet-4-6", 0, 0) == 0.0

    def test_cost_is_positive(self) -> None:
        assert _compute_cost("claude-sonnet-4-6", 100, 50) > 0


class TestFormatFileContents:
    def test_empty_returns_placeholder(self) -> None:
        assert _format_file_contents({}) == "(no files available)"

    def test_single_file(self) -> None:
        result = _format_file_contents({"foo.py": "x = 1"})
        assert "foo.py" in result
        assert "x = 1" in result

    def test_multiple_files(self) -> None:
        result = _format_file_contents({"a.py": "x=1", "b.py": "y=2"})
        assert "a.py" in result
        assert "b.py" in result


class TestFormatPatchHistory:
    def test_empty_returns_placeholder(self) -> None:
        assert _format_patch_history([]) == "(no previous patches)"

    def test_single_patch(self) -> None:
        result = _format_patch_history(["--- a/foo.py\n+++ b/foo.py\n"])
        assert "Attempt 1" in result

    def test_multiple_patches(self) -> None:
        result = _format_patch_history(["patch1", "patch2"])
        assert "Attempt 1" in result
        assert "Attempt 2" in result


class TestGeneratePatch:
    def test_calls_anthropic_and_returns_patch(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-return a - b\n+return a + b\n")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state()

        with patch("agent.nodes.patch_generator.anthropic.Anthropic", return_value=mock_client):
            result = generate_patch(state)

        assert "current_patch" in result
        assert result["current_patch"] != ""
        assert result["llm_calls"] == 1

    def test_accumulates_token_usage(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="--- a/x.py\n+++ b/x.py\n")]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 80

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(token_usage={"prompt": 50, "completion": 10})

        with patch("agent.nodes.patch_generator.anthropic.Anthropic", return_value=mock_client):
            result = generate_patch(state)

        assert result["token_usage"]["prompt"] == 250  # 50 + 200
        assert result["token_usage"]["completion"] == 90  # 10 + 80

    def test_appends_patch_to_history(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="--- a/x.py\n+++ b/x.py\n")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(patch_history=["existing_patch"])

        with patch("agent.nodes.patch_generator.anthropic.Anthropic", return_value=mock_client):
            result = generate_patch(state)

        assert len(result["patch_history"]) == 2

    def test_cannot_fix_not_appended_to_history(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="CANNOT_FIX — unsolvable")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state()

        with patch("agent.nodes.patch_generator.anthropic.Anthropic", return_value=mock_client):
            result = generate_patch(state)

        # CANNOT_FIX should not be appended to patch history
        assert result["patch_history"] == []

    def test_includes_critique_in_prompt(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="--- a/x.py\n+++ b/x.py\n")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(reflection_critique="REVISE — patch masks symptom, not root cause")

        with patch("agent.nodes.patch_generator.anthropic.Anthropic", return_value=mock_client):
            generate_patch(state)

        # Verify the critique was included in the user message
        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Self-Reflection Critique" in user_content
