"""Docker sandbox manager for safe execution of untrusted patches.

This is the highest-risk module — it applies code written by an LLM to a
repository and runs the test suite. The container has:
  - no network access
  - 512 MB memory cap
  - 1 CPU limit
  - 60-second timeout
  - read-only repo mount; all writes go to an in-container tmpdir

Never allow host filesystem writes. Never mount the Docker socket.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import docker
import docker.errors

from sandbox import SandboxUnavailableError
from agent.state import AgentState

logger = logging.getLogger(__name__)

_SANDBOX_IMAGE = "self-healing-agent-sandbox:latest"
_CONTAINER_TIMEOUT = 60  # seconds
_MEM_LIMIT = "512m"
_CPU_QUOTA = 100_000  # 1 CPU = 100_000 µs per 100_000 µs period


def _get_docker_client() -> docker.DockerClient:
    """Return a Docker client, raising SandboxUnavailableError if daemon is down."""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except docker.errors.DockerException as exc:
        raise SandboxUnavailableError(
            f"Docker daemon unreachable: {exc}. "
            "Ensure Docker is running and the current user has permission."
        ) from exc


def _ensure_image(client: docker.DockerClient) -> None:
    """Verify the sandbox image exists; raise SandboxUnavailableError if not."""
    try:
        client.images.get(_SANDBOX_IMAGE)
    except docker.errors.ImageNotFound:
        raise SandboxUnavailableError(
            f"Sandbox image '{_SANDBOX_IMAGE}' not found. "
            "Build it with: docker build -t self-healing-agent-sandbox:latest ./sandbox/"
        )


def execute_patch(state: AgentState) -> dict[str, Any]:
    """Apply the current patch and run the failing tests inside a Docker container.

    Node function: receives full AgentState, returns partial update dict.

    The iteration counter is incremented here so that routing logic after
    this node sees the updated value.

    Returns:
        {
            "test_results": str,
            "tests_passed": bool,
            "iteration": incremented int,
            "status": "running" | "success",
        }
    """
    current_patch = state.get("current_patch", "")
    repo_path = state["repo_path"]
    failing_tests = state.get("failing_tests", [])
    new_iteration = state.get("iteration", 0) + 1

    if not current_patch or current_patch.startswith("CANNOT_FIX"):
        logger.warning("No valid patch to execute — skipping sandbox")
        return {
            "test_results": f"PATCH_APPLY_FAILED: {current_patch or 'empty patch'}",
            "tests_passed": False,
            "iteration": new_iteration,
            "status": "running",
        }

    # Write patch to a temp file the container can read (read-only mount)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".diff",
        delete=False,
        encoding="utf-8",
    ) as tmp_patch:
        tmp_patch.write(current_patch)
        patch_host_path = tmp_patch.name

    try:
        return _run_container(
            repo_path=repo_path,
            patch_host_path=patch_host_path,
            failing_tests=failing_tests,
            new_iteration=new_iteration,
        )
    finally:
        # Always clean up the temp patch file on the host
        try:
            os.unlink(patch_host_path)
        except OSError:
            pass


def _run_container(
    repo_path: str,
    patch_host_path: str,
    failing_tests: list[str],
    new_iteration: int,
) -> dict[str, Any]:
    """Spin up the sandbox container and return execution results."""
    client = _get_docker_client()
    _ensure_image(client)

    test_files_str = " ".join(
        f'"{t}"' if " " in t else t for t in failing_tests
    )

    # Shell command executed inside the container:
    # 1. Copy read-only repo mount to writable tmpdir
    # 2. Apply the patch
    # 3. Install dependencies if requirements.txt or setup.py exists
    # 4. Run pytest
    bash_cmd = (
        "set -e && "
        "cp -r /repo /tmp/workdir && "
        "cd /tmp/workdir && "
        "patch -p1 < /tmp/patch.diff && "
        "{ [ -f requirements.txt ] && pip install -q -r requirements.txt; true; } && "
        "{ [ -f setup.py ] && pip install -q -e .; true; } && "
        f"python -m pytest {test_files_str} --tb=short -q --no-header"
    )

    logger.info(
        "Running sandbox — iteration=%d repo=%s tests=%s",
        new_iteration,
        repo_path,
        failing_tests,
    )

    volumes: dict[str, dict[str, str]] = {
        str(Path(repo_path).resolve()): {"bind": "/repo", "mode": "ro"},
        str(Path(patch_host_path).resolve()): {"bind": "/tmp/patch.diff", "mode": "ro"},
    }

    try:
        output_bytes: bytes = client.containers.run(
            image=_SANDBOX_IMAGE,
            command=["bash", "-c", bash_cmd],
            volumes=volumes,
            network_disabled=True,
            mem_limit=_MEM_LIMIT,
            cpu_quota=_CPU_QUOTA,
            remove=True,
            detach=False,
            stdout=True,
            stderr=True,
            timeout=_CONTAINER_TIMEOUT,
        )
        output = output_bytes.decode("utf-8", errors="replace") if output_bytes else ""
        tests_passed = _infer_pass(output)

        logger.info(
            "Sandbox finished — iteration=%d passed=%s output_len=%d",
            new_iteration,
            tests_passed,
            len(output),
        )

        return {
            "test_results": output,
            "tests_passed": tests_passed,
            "iteration": new_iteration,
            "status": "success" if tests_passed else "running",
        }

    except docker.errors.ContainerError as exc:
        # Non-zero exit code from the container — expected on test failure
        output = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        tests_passed = False
        logger.info("Container exited non-zero (iteration %d): %s", new_iteration, output[:200])
        return {
            "test_results": output,
            "tests_passed": tests_passed,
            "iteration": new_iteration,
            "status": "running",
        }

    except docker.errors.APIError as exc:
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            msg = f"TIMEOUT after {_CONTAINER_TIMEOUT}s"
            logger.warning("Sandbox timeout on iteration %d", new_iteration)
        else:
            msg = f"DOCKER_API_ERROR: {exc}"
            logger.error("Docker API error on iteration %d: %s", new_iteration, exc)
        return {
            "test_results": msg,
            "tests_passed": False,
            "iteration": new_iteration,
            "status": "running",
        }


def _infer_pass(pytest_output: str) -> bool:
    """Heuristic: pytest passed if output contains a passing-summary line."""
    lower = pytest_output.lower()
    # pytest prints "X passed" on success; "X failed" / "error" on failure
    if " passed" in lower and " failed" not in lower and "error" not in lower:
        return True
    # Also handle the case of "no tests ran" and "0 failed" as non-pass
    return False
