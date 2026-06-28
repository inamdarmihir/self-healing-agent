"""LangGraph StateGraph definition for the self-healing code agent.

Topology:
    START
      → context_builder
      → patch_generator
      → self_reflector
      → [conditional] if not approved AND iter < 2 → patch_generator
                       else → sandbox_executor
      → [conditional] if passed        → pr_opener → END
                       if max_iter     → postmortem_generator → END
                       else            → patch_generator
"""

import logging
import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.edges import route_after_execution, route_after_reflection
from agent.nodes.context_builder import build_context
from agent.nodes.patch_generator import generate_patch
from agent.nodes.postmortem import generate_postmortem
from agent.nodes.self_reflector import reflect_on_patch
from agent.state import AgentState
from github_integration.pr_opener import open_pr
from sandbox.executor import execute_patch

logger = logging.getLogger(__name__)


def _build_graph() -> Any:
    """Assemble and compile the LangGraph StateGraph.

    Returns the compiled runnable — call .invoke(initial_state) to run.
    """
    workflow: StateGraph = StateGraph(AgentState)

    # --- Nodes ---
    workflow.add_node("context_builder", build_context)
    workflow.add_node("patch_generator", generate_patch)
    workflow.add_node("self_reflector", reflect_on_patch)
    workflow.add_node("sandbox_executor", execute_patch)
    workflow.add_node("pr_opener", open_pr)
    workflow.add_node("postmortem_generator", generate_postmortem)

    # --- Linear edges ---
    workflow.add_edge(START, "context_builder")
    workflow.add_edge("context_builder", "patch_generator")
    workflow.add_edge("patch_generator", "self_reflector")

    # --- Conditional edges ---
    workflow.add_conditional_edges(
        "self_reflector",
        route_after_reflection,
        {
            "patch_generator": "patch_generator",
            "sandbox_executor": "sandbox_executor",
        },
    )

    workflow.add_conditional_edges(
        "sandbox_executor",
        route_after_execution,
        {
            "pr_opener": "pr_opener",
            "patch_generator": "patch_generator",
            "postmortem_generator": "postmortem_generator",
        },
    )

    # --- Terminal edges ---
    workflow.add_edge("pr_opener", END)
    workflow.add_edge("postmortem_generator", END)

    return workflow.compile()


# Module-level compiled graph — import this in scripts and eval harnesses
graph = _build_graph()


def run_task(
    task_id: str,
    repo_path: str,
    failing_tests: list[str],
    issue_description: str,
    max_iterations: int | None = None,
) -> AgentState:
    """Run the agent on a single repair task.

    Args:
        task_id: Unique identifier for this task (e.g. SWE-bench instance ID).
        repo_path: Absolute path to the repository on disk.
        failing_tests: Relative paths (from repo root) to failing test files.
        issue_description: Human-readable description of the bug.
        max_iterations: Hard cap on sandbox execution attempts (default: env var or 5).

    Returns:
        Final AgentState after the graph terminates.
    """
    cap = max_iterations or int(os.getenv("AGENT_MAX_ITERATIONS", "5"))

    initial_state: AgentState = {
        "task_id": task_id,
        "repo_path": repo_path,
        "failing_tests": failing_tests,
        "issue_description": issue_description,
        "relevant_files": [],
        "file_contents": {},
        "current_patch": None,
        "patch_history": [],
        "reflection_critique": None,
        "reflection_approved": False,
        "test_results": None,
        "tests_passed": False,
        "iteration": 0,
        "max_iterations": cap,
        "token_usage": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "llm_calls": 0,
        "status": "running",
        "pr_url": None,
        "postmortem": None,
    }

    logger.info("Starting agent — task=%s repo=%s max_iter=%d", task_id, repo_path, cap)
    final_state: AgentState = graph.invoke(initial_state)  # type: ignore[assignment]
    logger.info(
        "Agent finished — task=%s status=%s cost=$%.4f iterations=%d",
        task_id,
        final_state.get("status"),
        final_state.get("cost_usd", 0.0),
        final_state.get("iteration", 0),
    )
    return final_state
