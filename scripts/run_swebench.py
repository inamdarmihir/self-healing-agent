#!/usr/bin/env python3
"""CLI for running the full SWE-bench Lite evaluation.

Usage:
    # Smoke test: first 5 tasks
    python scripts/run_swebench.py --limit 5

    # Full evaluation: all 300 tasks
    python scripts/run_swebench.py

    # Save to a custom results path
    python scripts/run_swebench.py --output results/run_2024-01-01.json
"""

import logging
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.metrics import format_summary_table  # noqa: E402
from eval.swebench_runner import _RESULTS_PATH, run_evaluation  # noqa: E402

app = typer.Typer(
    name="run-swebench",
    help="Run the self-healing agent on SWE-bench Lite.",
    add_completion=False,
)
console = Console()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def main(
    limit: int = typer.Option(
        None,
        "--limit",
        "-n",
        help="Max tasks to evaluate. Omit for full 300-task eval.",
    ),
    output: Path = typer.Option(
        _RESULTS_PATH,
        "--output",
        "-o",
        help="Path to write benchmark_results.json",
    ),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Run the self-healing agent against SWE-bench Lite."""
    _configure_logging(log_level)

    n_tasks = limit if limit is not None else 300
    console.print(
        Panel(
            f"[bold]Tasks to evaluate:[/bold] {n_tasks}\n"
            f"[bold]Results will be saved to:[/bold] {output}\n\n"
            "[yellow]Tip:[/yellow] Results are saved incrementally after each task.\n"
            "You can safely interrupt with Ctrl+C and resume by examining the JSON.",
            title="[bold blue]SWE-bench Lite Evaluation[/bold blue]",
        )
    )

    if limit is not None and limit < 5:  # noqa: PLR2004
        console.print(
            "[yellow]Warning:[/yellow] Running fewer than 5 tasks gives unreliable pass@1 estimates."
        )

    try:
        summary = run_evaluation(limit=limit, results_path=output)
    except KeyboardInterrupt:
        console.print("\n[yellow]Evaluation interrupted by user.[/yellow]")
        raise typer.Exit(130) from None
    except ImportError as exc:
        console.print(f"[red]Missing dependency:[/red] {exc}")
        console.print("Install with: pip install datasets swebench")
        raise typer.Exit(1) from exc

    console.print("\n[bold green]Evaluation complete![/bold green]\n")
    console.print(summary.pass_at_1, f"— {summary.passed}/{summary.total_tasks} tasks passed")
    console.print(format_summary_table(summary))
    console.print(f"\nFull results: [bold]{output}[/bold]")

    raise typer.Exit(0)


if __name__ == "__main__":
    app()
