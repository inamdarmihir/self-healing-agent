"""Tests for sandbox/executor.py.

Docker is not required for these tests — all container interactions are mocked.
Tests that exercise real Docker behavior belong in integration tests.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from agent.state import AgentState
from sandbox import SandboxUnavailableError
from sandbox.executor import _infer_pass, execute_patch
from tests.fixtures.sample_patch import CANNOT_FIX_RESPONSE, INVALID_PATCH, VALID_PATCH

_FIXTURE_REPO = str(Path(__file__).parent / "fixtures" / "sample_repo")


def _base_state(**overrides: object) -> AgentState:
    state: AgentState = {
        "task_id": "test-exec-001",
        "repo_path": _FIXTURE_REPO,
        "failing_tests": ["test_main.py"],
        "issue_description": "add() bug",
        "relevant_files": ["main.py"],
        "file_contents": {},
        "current_patch": VALID_PATCH,
        "patch_history": [],
        "reflection_critique": None,
        "reflection_approved": True,
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


class TestInferPass:
    def test_passed_output(self) -> None:
        assert _infer_pass("3 passed in 0.05s") is True

    def test_failed_output(self) -> None:
        assert _infer_pass("1 failed, 2 passed") is False

    def test_error_output(self) -> None:
        assert _infer_pass("ERROR collecting tests") is False

    def test_empty_output(self) -> None:
        assert _infer_pass("") is False

    def test_no_tests_run(self) -> None:
        assert _infer_pass("no tests ran") is False


class TestExecutePatch:
    def test_skips_empty_patch(self) -> None:
        state = _base_state(current_patch="")
        result = execute_patch(state)
        assert result["tests_passed"] is False
        assert "PATCH_APPLY_FAILED" in result["test_results"]
        assert result["iteration"] == 1  # still increments

    def test_skips_cannot_fix_patch(self) -> None:
        state = _base_state(current_patch=CANNOT_FIX_RESPONSE)
        result = execute_patch(state)
        assert result["tests_passed"] is False
        assert result["iteration"] == 1

    def test_increments_iteration(self) -> None:
        state = _base_state(iteration=2)
        with patch("sandbox.executor._get_docker_client") as mock_docker:
            mock_docker.side_effect = SandboxUnavailableError("mocked")
            # Even on error, iteration should increment (error bubbles up)
            with pytest.raises(SandboxUnavailableError):
                execute_patch(state)

    def test_successful_container_run(self) -> None:
        mock_client = MagicMock()
        mock_client.containers.run.return_value = b"3 passed in 0.12s\n"

        state = _base_state(current_patch=VALID_PATCH)

        with (
            patch("sandbox.executor._get_docker_client", return_value=mock_client),
            patch("sandbox.executor._ensure_image"),
        ):
            result = execute_patch(state)

        assert result["tests_passed"] is True
        assert result["iteration"] == 1
        assert result["status"] == "success"

    def test_container_error_non_zero_exit(self) -> None:
        mock_client = MagicMock()
        mock_client.containers.run.side_effect = docker.errors.ContainerError(
            container=MagicMock(),
            exit_status=1,
            command="pytest",
            image="sandbox",
            stderr=b"1 failed in 0.05s\n",
        )

        state = _base_state(current_patch=VALID_PATCH)

        with (
            patch("sandbox.executor._get_docker_client", return_value=mock_client),
            patch("sandbox.executor._ensure_image"),
        ):
            result = execute_patch(state)

        assert result["tests_passed"] is False
        assert result["iteration"] == 1

    def test_docker_daemon_unreachable_raises(self) -> None:
        state = _base_state(current_patch=VALID_PATCH)

        with patch(
            "sandbox.executor._get_docker_client",
            side_effect=SandboxUnavailableError("Docker down"),
        ):
            with pytest.raises(SandboxUnavailableError):
                execute_patch(state)

    def test_timeout_returns_timeout_message(self) -> None:
        mock_client = MagicMock()
        mock_client.containers.run.side_effect = docker.errors.APIError("timed out")

        state = _base_state(current_patch=VALID_PATCH)

        with (
            patch("sandbox.executor._get_docker_client", return_value=mock_client),
            patch("sandbox.executor._ensure_image"),
        ):
            result = execute_patch(state)

        assert "TIMEOUT" in result["test_results"]
        assert result["tests_passed"] is False
