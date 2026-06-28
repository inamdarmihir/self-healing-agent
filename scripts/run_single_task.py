#!/usr/bin/env python3
"""CLI for running the self-healing agent on a single repair task.

Usage examples:
    python scripts/run_single_task.py \\
        --task-id my-bug-001 \\
        --repo-path /path/to/repo \\
        --failing-tests tests/test_foo.py tests/test_bar.py \\
        --issue "The add() function returns the wrong value when inputs are negative"

    # Dry run (no PR will be opened):
    DRY_RUN=true python scripts/run_single_task.py ...
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.graph import run_task  # noqa: E402
from eval.metrics import TaskResult, format_summary_table, compute_task_metrics  # noqa: E402

app = typer.Typer(
    name="run-single-task",
    help="Run the self-healing agent on one repository.",
    add_completion=False,
)
console = Console()
logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def main(
    task_id: str = typer.Option(..., "--task-id", "-t", help="Unique task identifier"),
    repo_path: str = typer.Option(..., "--repo-path", "-r", help="Absolute path to the repo"),
    failing_tests: list[str] = typer.Option(
        ..., "--failing-tests", "-f", help="Relative paths to failing test files (repeatable)"
    ),
    issue: str = typer.Option("", "--issue", "-i", help="Human-readable issue description"),
    max_iterations: int = typer.Option(
        5, "--max-iter", "-m", help="Hard cap on sandbox attempts"
    ),
    save_result: bool = typer.Option(
        True, "--save/--no-save", help="Save result JSON to results/ directory"
    ),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Run the self-healing agent on a single task."""
    _configure_logging(log_level)

    if not Path(repo_path).exists():
        console.print(f"[red]Error:[/red] repo-path does not exist: {repo_path}")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold]Task:[/bold] {task_id}\n"
            f"[bold]Repo:[/bold] {repo_path}\n"
            f"[bold]Tests:[/bold] {', '.join(failing_tests)}\n"
            f"[bold]Max iterations:[/bold] {max_iterations}",
            title="[bold blue]Self-Healing Agent[/bold blue]",
        )
    )

    start = time.monotonic()

    try:
        final_state = run_task(
            task_id=task_id,
            repo_path=repo_path,
            failing_tests=failing_tests,
            issue_description=issue,
            max_iterations=max_iterations,
        )
    except Exception as exc:
        console.print(f"[red]Agent raised an unhandled exception:[/red] {exc}")
        logging.exception("Unhandled exception during task %s", task_id)
        raise typer.Exit(2) from exc

    elapsed = time.monotonic() - start

    # --- Summary table ---
    token_usage = final_state.get("token_usage", {})
    total_tokens = token_usage.get("prompt", 0) + token_usage.get("completion", 0)

    table = Table(title="Result", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="dim")
    table.add_column("Value")

    status = final_state.get("status", "unknown")
    status_color = "green" if status == "success" else "red"

    table.add_row("Status", f"[{status_color}]{status}[/{status_color}]")
    table.add_row("Tests passed", "✓" if final_state.get("tests_passed") else "✗")
    table.add_row("Iterations", str(final_state.get("iteration", 0)))
    table.add_row("LLM calls", str(final_state.get("llm_calls", 0)))
    table.add_row("Total tokens", f"{total_tokens:,}")
    table.add_row("Cost", f"${final_state.get('cost_usd', 0.0):.4f}")
    table.add_row("Wall time", f"{elapsed:.1f}s")

    if final_state.get("pr_url"):
        table.add_row("PR URL", final_state["pr_url"])

    console.print(table)

    if final_state.get("postmortem"):
        console.print(Panel(final_state["postmortem"], title="[yellow]Post-Mortem[/yellow]"))

    # --- Save result ---
    if save_result:
        results_dir = Path(os.getenv("RESULTS_DIR", "./results"))
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = results_dir / f"{task_id}.json"

        task_result = TaskResult(
            task_id=task_id,
            status=status,
            iterations=final_state.get("iteration", 0),
            cost_usd=final_state.get("cost_usd", 0.0),
            token_usage=token_usage,
            llm_calls=final_state.get("llm_calls", 0),
            tests_passed=final_state.get("tests_passed", False),
            time_seconds=elapsed,
            postmortem=final_state.get("postmortem"),
            pr_url=final_state.get("pr_url"),
        )

        from dataclasses import asdict
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(asdict(task_result), f, indent=2)

        console.print(f"Result saved to [bold]{result_path}[/bold]")

    raise typer.Exit(0 if final_state.get("tests_passed") else 1)


if __name__ == "__main__":
    app()
