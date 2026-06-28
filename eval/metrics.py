"""All evaluation metrics for benchmarking the self-healing agent.

Used by both the SWE-bench runner and the single-task CLI.
Implements the unbiased pass@k estimator from Chen et al. 2021 (Codex paper).
"""

import math
from dataclasses import dataclass, field


@dataclass
class TaskResult:
    """Result record for a single repair task."""

    task_id: str
    status: str  # "success" | "failed" | "max_iterations"
    iterations: int
    cost_usd: float
    token_usage: dict[str, int]
    llm_calls: int
    tests_passed: bool
    time_seconds: float
    postmortem: str | None = field(default=None)
    pr_url: str | None = field(default=None)
    error: str | None = field(default=None)  # unhandled exception, if any


@dataclass
class BenchmarkSummary:
    """Aggregate metrics across all evaluated tasks."""

    total_tasks: int
    passed: int
    failed: int
    pass_at_1: float  # primary headline metric
    avg_iterations: float
    avg_cost_usd: float
    total_cost_usd: float
    avg_time_seconds: float
    cost_per_success: float
    total_tokens: int
    avg_llm_calls: float


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator from the Codex paper (Chen et al. 2021).

    Computes: 1 - C(n-c, k) / C(n, k)

    Args:
        n: Total number of samples generated per problem.
        c: Number of correct samples (those that pass all tests).
        k: k in pass@k.

    Returns:
        Estimated probability that at least one of k samples is correct.

    Notes:
        - When c == 0, returns 0.0 (no correct samples → can't pass).
        - When c >= n, returns 1.0 (all samples correct → definitely passes).
        - Uses the numerically stable form with math.comb to avoid overflow.
    """
    if n < k:
        raise ValueError(f"n ({n}) must be >= k ({k})")
    if c < 0 or c > n:
        raise ValueError(f"c ({c}) must be in [0, n={n}]")
    if c == 0:
        return 0.0
    if c >= n:
        return 1.0
    # Numerically stable: C(n-c, k) / C(n, k)
    # = prod_{i=0}^{k-1} (n-c-i)/(n-i)
    # Avoid computing huge binomial coefficients directly.
    result = 1.0
    for i in range(k):
        result *= (n - c - i) / (n - i)
    return 1.0 - result


def compute_task_metrics(results: list[TaskResult]) -> BenchmarkSummary:
    """Aggregate metrics across all evaluated tasks.

    Args:
        results: List of TaskResult objects from the evaluation run.

    Returns:
        BenchmarkSummary with all aggregate statistics.
    """
    if not results:
        return BenchmarkSummary(
            total_tasks=0,
            passed=0,
            failed=0,
            pass_at_1=0.0,
            avg_iterations=0.0,
            avg_cost_usd=0.0,
            total_cost_usd=0.0,
            avg_time_seconds=0.0,
            cost_per_success=0.0,
            total_tokens=0,
            avg_llm_calls=0.0,
        )

    n = len(results)
    passed_results = [r for r in results if r.tests_passed]
    c = len(passed_results)

    total_cost = sum(r.cost_usd for r in results)
    total_tokens = sum(
        r.token_usage.get("prompt", 0) + r.token_usage.get("completion", 0)
        for r in results
    )

    return BenchmarkSummary(
        total_tasks=n,
        passed=c,
        failed=n - c,
        pass_at_1=pass_at_k(n=n, c=c, k=1),
        avg_iterations=sum(r.iterations for r in results) / n,
        avg_cost_usd=total_cost / n,
        total_cost_usd=total_cost,
        avg_time_seconds=sum(r.time_seconds for r in results) / n,
        cost_per_success=cost_per_successful_task(results),
        total_tokens=total_tokens,
        avg_llm_calls=sum(r.llm_calls for r in results) / n,
    )


def cost_per_successful_task(results: list[TaskResult]) -> float:
    """Total cost divided by number of tasks where tests_passed=True.

    Returns math.inf if no tasks succeeded (meaningful, not an error).
    """
    passed = [r for r in results if r.tests_passed]
    if not passed:
        return math.inf
    total_cost = sum(r.cost_usd for r in results)
    return total_cost / len(passed)


def format_summary_table(summary: BenchmarkSummary) -> str:
    """Render a BenchmarkSummary as a markdown table for CLI output."""
    cost_per = (
        f"${summary.cost_per_success:.4f}"
        if math.isfinite(summary.cost_per_success)
        else "N/A (0 successes)"
    )
    return (
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| **pass@1 on SWE-bench Lite** | **{summary.pass_at_1:.1%}** "
        f"({summary.passed}/{summary.total_tasks} tasks) |\n"
        f"| Average iterations per task | {summary.avg_iterations:.1f} |\n"
        f"| Average cost per task | ${summary.avg_cost_usd:.4f} |\n"
        f"| Cost per successful fix | {cost_per} |\n"
        f"| Average time per task | {summary.avg_time_seconds:.0f}s |\n"
        f"| Total tokens used | {summary.total_tokens:,} |\n"
        f"| Total cost | ${summary.total_cost_usd:.4f} |\n"
    )
