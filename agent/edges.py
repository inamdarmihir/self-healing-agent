"""Conditional edge logic for the LangGraph state machine.

Each function receives the current state and returns the name of the next node.
Edge functions must be pure — no side effects, no state mutations.
"""

import logging

from agent.state import AgentState

logger = logging.getLogger(__name__)


def route_after_reflection(state: AgentState) -> str:
    """Route after self_reflector: either revise the patch or proceed to sandbox.

    Returns:
        "patch_generator"  — reflection rejected and still in early iterations.
        "sandbox_executor" — approved, or past the reflection window (iter >= 2).
    """
    approved = state.get("reflection_approved", True)
    iteration = state.get("iteration", 0)

    if not approved and iteration < 2:
        logger.info(
            "Reflection rejected patch on iteration %d — routing back to patch_generator",
            iteration,
        )
        return "patch_generator"

    logger.info(
        "Reflection approved (or skipped) on iteration %d — routing to sandbox_executor",
        iteration,
    )
    return "sandbox_executor"


def route_after_execution(state: AgentState) -> str:
    """Route after sandbox_executor: success, retry, or postmortem.

    The executor increments state["iteration"] before returning, so the
    comparison here is against the already-incremented value.

    Returns:
        "pr_opener"            — tests passed.
        "patch_generator"      — tests failed but iterations remain.
        "postmortem_generator" — tests failed and max iterations reached.
    """
    tests_passed = state.get("tests_passed", False)
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 5)

    if tests_passed:
        logger.info("Tests passed on iteration %d — routing to pr_opener", iteration)
        return "pr_opener"

    if iteration >= max_iterations:
        logger.info(
            "Max iterations (%d) reached — routing to postmortem_generator",
            max_iterations,
        )
        return "postmortem_generator"

    logger.info(
        "Tests failed on iteration %d/%d — routing back to patch_generator",
        iteration,
        max_iterations,
    )
    return "patch_generator"
