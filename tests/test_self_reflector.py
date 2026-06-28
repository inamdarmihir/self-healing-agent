"""Tests for agent/nodes/self_reflector.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.nodes.self_reflector import reflect_on_patch
from agent.state import AgentState
from tests.fixtures.sample_patch import CANNOT_FIX_RESPONSE, VALID_PATCH, WRONG_FIX_PATCH

_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def _base_state(**overrides: object) -> AgentState:
    state: AgentState = {
        "task_id": "test-reflect-001",
        "repo_path": str(_FIXTURE_REPO),
        "failing_tests": ["test_main.py"],
        "issue_description": "add() bug",
        "relevant_files": ["main.py"],
        "file_contents": {
            "test_main.py": "def test_add(): assert add(2,3)==5",
            "main.py": "def add(a,b): return a-b",
        },
        "current_patch": VALID_PATCH,
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


class TestReflectOnPatch:
    def test_skips_llm_on_iteration_2(self) -> None:
        """Reflection is cost-controlled: no LLM call on iteration >= 2."""
        state = _base_state(iteration=2)

        with patch("agent.nodes.self_reflector.anthropic.Anthropic") as mock_cls:
            result = reflect_on_patch(state)

        mock_cls.assert_not_called()
        assert result["reflection_approved"] is True
        assert result["reflection_critique"] is None

    def test_skips_llm_on_iteration_3(self) -> None:
        state = _base_state(iteration=3)
        with patch("agent.nodes.self_reflector.anthropic.Anthropic") as mock_cls:
            result = reflect_on_patch(state)
        mock_cls.assert_not_called()
        assert result["reflection_approved"] is True

    def test_auto_approves_empty_patch(self) -> None:
        state = _base_state(current_patch="", iteration=0)
        with patch("agent.nodes.self_reflector.anthropic.Anthropic") as mock_cls:
            result = reflect_on_patch(state)
        mock_cls.assert_not_called()
        assert result["reflection_approved"] is True

    def test_auto_approves_cannot_fix_patch(self) -> None:
        state = _base_state(current_patch=CANNOT_FIX_RESPONSE, iteration=0)
        with patch("agent.nodes.self_reflector.anthropic.Anthropic") as mock_cls:
            result = reflect_on_patch(state)
        mock_cls.assert_not_called()
        assert result["reflection_approved"] is True

    def test_approved_verdict_sets_flag(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="APPROVED")]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(iteration=0, current_patch=VALID_PATCH)

        with patch("agent.nodes.self_reflector.anthropic.Anthropic", return_value=mock_client):
            result = reflect_on_patch(state)

        assert result["reflection_approved"] is True
        assert result["reflection_critique"] is None

    def test_revise_verdict_sets_critique(self) -> None:
        critique_text = "REVISE — patch masks symptom, not root cause"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=critique_text)]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 20

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(iteration=1, current_patch=WRONG_FIX_PATCH)

        with patch("agent.nodes.self_reflector.anthropic.Anthropic", return_value=mock_client):
            result = reflect_on_patch(state)

        assert result["reflection_approved"] is False
        assert result["reflection_critique"] == critique_text

    def test_accumulates_token_usage(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="APPROVED")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 10

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(
            iteration=0,
            current_patch=VALID_PATCH,
            token_usage={"prompt": 200, "completion": 30},
        )

        with patch("agent.nodes.self_reflector.anthropic.Anthropic", return_value=mock_client):
            result = reflect_on_patch(state)

        assert result["token_usage"]["prompt"] == 300
        assert result["token_usage"]["completion"] == 40

    def test_increments_llm_calls(self) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="APPROVED")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 2

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        state = _base_state(iteration=0, current_patch=VALID_PATCH, llm_calls=3)

        with patch("agent.nodes.self_reflector.anthropic.Anthropic", return_value=mock_client):
            result = reflect_on_patch(state)

        assert result["llm_calls"] == 4
