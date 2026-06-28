"""SWE-bench Lite evaluation harness.

Loads tasks from the SWE-bench Lite dataset, runs the agent on each,
and writes structured results to eval/benchmark_results.json.

Usage:
    python scripts/run_swebench.py --limit 5   # smoke test on 5 tasks
    python scripts/run_swebench.py             # full 300-task evaluation
"""

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path

from eval.metrics import BenchmarkSummary, TaskResult, compute_task_metrics, format_summary_table

logger = logging.getLogger(__name__)

_RESULTS_PATH = Path(__file__).parent / "benchmark_results.json"


def _load_swebench_tasks(limit: int | None = None) -> list[dict]:  # type: ignore[type-arg]
    """Load SWE-bench Lite tasks.

    Requires the `swebench` package (pip install swebench) and the dataset
    to be available via HuggingFace datasets.

    Args:
        limit: If set, only load the first N tasks (useful for smoke tests).

    Returns:
        List of task dicts with keys: instance_id, repo, base_commit,
        problem_statement, FAIL_TO_PASS, PASS_TO_PASS, etc.
    """
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "Install the 'datasets' package to load SWE-bench: "
            "pip install datasets swebench"
        ) from exc

    logger.info("Loading SWE-bench Lite dataset from HuggingFace...")
    dataset = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    tasks = list(dataset)  # type: ignore[arg-type]

    if limit is not None:
        tasks = tasks[:limit]

    logger.info("Loaded %d SWE-bench Lite tasks", len(tasks))
    return tasks  # type: ignore[return-value]


def _prepare_repo(task: dict) -> str:  # type: ignore[type-arg]
    """Clone and checkout the task's base commit into a temp directory.

    Returns the absolute path to the checked-out repo.
    """
    import subprocess
    import tempfile

    repo_url = f"https://github.com/{task['repo']}.git"
    base_commit = task["base_commit"]
    workdir = tempfile.mkdtemp(prefix="swebench_")

    logger.info("Cloning %s @ %s into %s", task["repo"], base_commit, workdir)

    subprocess.run(
        ["git", "clone", "--depth=50", repo_url, workdir],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", base_commit],
        check=True,
        capture_output=True,
        cwd=workdir,
    )
    return workdir


def _extract_failing_tests(task: dict) -> list[str]:  # type: ignore[type-arg]
    """Extract test file paths from SWE-bench FAIL_TO_PASS list."""
    fail_to_pass = task.get("FAIL_TO_PASS", "[]")
    if isinstance(fail_to_pass, str):
        import ast
        tests = ast.literal_eval(fail_to_pass)
    else:
        tests = list(fail_to_pass)

    # SWE-bench test IDs look like: "tests/test_foo.py::TestClass::test_method"
    # Extract unique file paths
    files: list[str] = []
    seen: set[str] = set()
    for test_id in tests:
        file_path = test_id.split("::")[0]
        if file_path not in seen:
            files.append(file_path)
            seen.add(file_path)
    return files


def run_single_task(task: dict, repo_path: str) -> TaskResult:  # type: ignore[type-arg]
    """Run the agent on one SWE-bench task and return a TaskResult."""
    from agent.graph import run_task

    task_id = task["instance_id"]
    failing_tests = _extract_failing_tests(task)
    issue_description = task.get("problem_statement", "")

    start = time.monotonic()
    error: str | None = None

    try:
        final_state = run_task(
            task_id=task_id,
            repo_path=repo_path,
            failing_tests=failing_tests,
            issue_description=issue_description,
        )
        elapsed = time.monotonic() - start
        return TaskResult(
            task_id=task_id,
            status=final_state.get("status", "failed"),
            iterations=final_state.get("iteration", 0),
            cost_usd=final_state.get("cost_usd", 0.0),
            token_usage=final_state.get("token_usage", {}),
            llm_calls=final_state.get("llm_calls", 0),
            tests_passed=final_state.get("tests_passed", False),
            time_seconds=elapsed,
            postmortem=final_state.get("postmortem"),
            pr_url=final_state.get("pr_url"),
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - start
        logger.exception("Unhandled error on task %s: %s", task_id, exc)
        error = str(exc)
        return TaskResult(
            task_id=task_id,
            status="failed",
            iterations=0,
            cost_usd=0.0,
            token_usage={},
            llm_calls=0,
            tests_passed=False,
            time_seconds=elapsed,
            error=error,
        )


def run_evaluation(
    limit: int | None = None,
    results_path: Path = _RESULTS_PATH,
) -> BenchmarkSummary:
    """Run the full (or limited) SWE-bench Lite evaluation.

    Args:
        limit: Max number of tasks to evaluate. None = all 300.
        results_path: Where to write benchmark_results.json.

    Returns:
        BenchmarkSummary with aggregate metrics.
    """
    tasks = _load_swebench_tasks(limit=limit)
    results: list[TaskResult] = []

    for i, task in enumerate(tasks, 1):
        task_id = task["instance_id"]
        logger.info("Task %d/%d — %s", i, len(tasks), task_id)

        try:
            repo_path = _prepare_repo(task)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to prepare repo for %s: %s", task_id, exc)
            results.append(
                TaskResult(
                    task_id=task_id,
                    status="failed",
                    iterations=0,
                    cost_usd=0.0,
                    token_usage={},
                    llm_calls=0,
                    tests_passed=False,
                    time_seconds=0.0,
                    error=f"repo_prep_failed: {exc}",
                )
            )
            continue

        result = run_single_task(task, repo_path)
        results.append(result)

        # Persist incrementally so a crash doesn't lose all results
        _save_results(results, results_path)

        logger.info(
            "Progress: %d/%d — pass@1=%.1f%% cost=$%.4f",
            i,
            len(tasks),
            sum(r.tests_passed for r in results) / len(results) * 100,
            sum(r.cost_usd for r in results),
        )

    summary = compute_task_metrics(results)
    _save_results(results, results_path, summary=summary)

    logger.info("\n%s", format_summary_table(summary))
    return summary


def _save_results(
    results: list[TaskResult],
    path: Path,
    summary: BenchmarkSummary | None = None,
) -> None:
    """Write results to JSON, creating parent directories as needed."""
    import math

    path.parent.mkdir(parents=True, exist_ok=True)

    def _clean(obj: object) -> object:
        """Make values JSON-serialisable (handle inf/nan from cost_per_success)."""
        if isinstance(obj, float) and not math.isfinite(obj):
            return None
        return obj

    payload: dict = {  # type: ignore[type-arg]
        "metadata": {
            "model": os.getenv("AGENT_MODEL", "claude-sonnet-4-6"),
            "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "swebench_split": "lite",
            "total_tasks_evaluated": len(results),
        },
        "tasks": [asdict(r) for r in results],
    }

    if summary is not None:
        raw_summary = asdict(summary)
        payload["summary"] = {k: _clean(v) for k, v in raw_summary.items()}

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    logger.debug("Results saved to %s", path)
