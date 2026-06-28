"""Tests for eval/metrics.py."""

import math

import pytest

from eval.metrics import (
    BenchmarkSummary,
    TaskResult,
    compute_task_metrics,
    cost_per_successful_task,
    format_summary_table,
    pass_at_k,
)


def _make_result(
    task_id: str = "t1",
    tests_passed: bool = True,
    iterations: int = 2,
    cost_usd: float = 0.01,
    time_seconds: float = 30.0,
    llm_calls: int = 3,
    status: str = "success",
) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=status if tests_passed else "failed",
        iterations=iterations,
        cost_usd=cost_usd,
        token_usage={"prompt": 100, "completion": 50},
        llm_calls=llm_calls,
        tests_passed=tests_passed,
        time_seconds=time_seconds,
    )


class TestPassAtK:
    def test_all_correct(self) -> None:
        # All n samples correct → pass@k = 1.0 for any valid k
        assert pass_at_k(n=10, c=10, k=1) == pytest.approx(1.0)
        assert pass_at_k(n=10, c=10, k=5) == pytest.approx(1.0)

    def test_none_correct(self) -> None:
        assert pass_at_k(n=10, c=0, k=1) == pytest.approx(0.0)
        assert pass_at_k(n=10, c=0, k=5) == pytest.approx(0.0)

    def test_k_equals_n(self) -> None:
        # k == n: pass@k = c/n (each sample independently)
        result = pass_at_k(n=5, c=2, k=5)
        assert 0.0 <= result <= 1.0

    def test_k_1_is_fraction(self) -> None:
        # pass@1 = c/n when n >> k
        result = pass_at_k(n=100, c=30, k=1)
        assert result == pytest.approx(0.30, abs=0.01)

    def test_monotone_in_c(self) -> None:
        # More correct samples → higher pass@k
        low = pass_at_k(n=10, c=2, k=1)
        high = pass_at_k(n=10, c=8, k=1)
        assert high > low

    def test_invalid_c_negative(self) -> None:
        with pytest.raises(ValueError):
            pass_at_k(n=10, c=-1, k=1)

    def test_invalid_c_gt_n(self) -> None:
        with pytest.raises(ValueError):
            pass_at_k(n=5, c=6, k=1)

    def test_invalid_k_gt_n(self) -> None:
        with pytest.raises(ValueError):
            pass_at_k(n=5, c=2, k=6)

    def test_k_equals_1_n_equals_1(self) -> None:
        assert pass_at_k(n=1, c=1, k=1) == pytest.approx(1.0)
        assert pass_at_k(n=1, c=0, k=1) == pytest.approx(0.0)


class TestCostPerSuccessfulTask:
    def test_all_success(self) -> None:
        results = [_make_result(cost_usd=0.02, tests_passed=True) for _ in range(5)]
        # total cost = 0.10, successes = 5 → cost_per = 0.10 / 5 = 0.02
        assert cost_per_successful_task(results) == pytest.approx(0.02)

    def test_no_success_returns_inf(self) -> None:
        results = [_make_result(tests_passed=False) for _ in range(3)]
        assert math.isinf(cost_per_successful_task(results))

    def test_mixed(self) -> None:
        results = [
            _make_result(cost_usd=0.10, tests_passed=True),
            _make_result(cost_usd=0.05, tests_passed=False),
            _make_result(cost_usd=0.08, tests_passed=True),
        ]
        # total = 0.23, successes = 2 → cost_per = 0.115
        assert cost_per_successful_task(results) == pytest.approx(0.115)


class TestComputeTaskMetrics:
    def test_empty_results(self) -> None:
        summary = compute_task_metrics([])
        assert summary.total_tasks == 0
        assert summary.pass_at_1 == 0.0

    def test_all_pass(self) -> None:
        results = [_make_result(tests_passed=True) for _ in range(10)]
        summary = compute_task_metrics(results)
        assert summary.total_tasks == 10
        assert summary.passed == 10
        assert summary.failed == 0
        assert summary.pass_at_1 == pytest.approx(1.0)

    def test_all_fail(self) -> None:
        results = [_make_result(tests_passed=False) for _ in range(10)]
        summary = compute_task_metrics(results)
        assert summary.passed == 0
        assert summary.pass_at_1 == pytest.approx(0.0)

    def test_avg_cost_calculation(self) -> None:
        results = [
            _make_result(cost_usd=0.10, tests_passed=True),
            _make_result(cost_usd=0.20, tests_passed=False),
        ]
        summary = compute_task_metrics(results)
        assert summary.avg_cost_usd == pytest.approx(0.15)
        assert summary.total_cost_usd == pytest.approx(0.30)

    def test_avg_iterations(self) -> None:
        results = [
            _make_result(iterations=1),
            _make_result(iterations=3),
        ]
        summary = compute_task_metrics(results)
        assert summary.avg_iterations == pytest.approx(2.0)

    def test_total_tokens(self) -> None:
        results = [_make_result() for _ in range(4)]  # each has prompt=100, completion=50
        summary = compute_task_metrics(results)
        assert summary.total_tokens == 4 * 150


class TestFormatSummaryTable:
    def test_returns_markdown_table(self) -> None:
        results = [_make_result() for _ in range(5)]
        summary = compute_task_metrics(results)
        table = format_summary_table(summary)
        assert "|" in table
        assert "pass@1" in table

    def test_infinite_cost_per_success(self) -> None:
        # When no tasks pass, cost_per_success = inf → should render as "N/A"
        results = [_make_result(tests_passed=False) for _ in range(3)]
        summary = compute_task_metrics(results)
        table = format_summary_table(summary)
        assert "N/A" in table
