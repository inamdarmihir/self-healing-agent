"""Pytest harness for use inside the Docker sandbox.

This module is intentionally minimal — it runs inside an isolated container
with no external dependencies beyond the standard library and pytest.
"""

import subprocess
import sys
from pathlib import Path


def run_tests(test_files: list[str], workdir: str) -> tuple[str, bool]:
    """Run pytest on the given test files.

    Args:
        test_files: Paths to test files, relative to workdir.
        workdir: Absolute path to the working directory inside the container.

    Returns:
        (combined_output, passed) — raw pytest stdout+stderr and exit-code bool.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_files,
        "--tb=short",
        "-q",
        "--no-header",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    return output, passed


def apply_patch(patch_path: str, workdir: str) -> tuple[str, bool]:
    """Apply a unified diff patch to the working directory.

    Args:
        patch_path: Absolute path to the .diff file.
        workdir: Directory where patch -p1 is executed.

    Returns:
        (output, success) — patch command output and success bool.
    """
    result = subprocess.run(
        ["patch", "-p1", "--input", patch_path],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    output = result.stdout + result.stderr
    success = result.returncode == 0
    return output, success


if __name__ == "__main__":
    # Entrypoint when invoked directly inside the container:
    # python -m sandbox.runner <patch_path> <workdir> <test_file> [<test_file> ...]
    import sys

    if len(sys.argv) < 4:  # noqa: PLR2004
        print("Usage: runner.py <patch_path> <workdir> <test_file>...", file=sys.stderr)
        sys.exit(2)

    _patch_path = sys.argv[1]
    _workdir = sys.argv[2]
    _test_files = sys.argv[3:]

    # Ensure workdir exists and is writable (it is /tmp/workdir inside container)
    Path(_workdir).mkdir(parents=True, exist_ok=True)

    patch_output, patch_ok = apply_patch(_patch_path, _workdir)
    if not patch_ok:
        print(f"PATCH_APPLY_FAILED:\n{patch_output}", file=sys.stderr)
        sys.exit(1)

    test_output, tests_ok = run_tests(_test_files, _workdir)
    print(test_output)
    sys.exit(0 if tests_ok else 1)
