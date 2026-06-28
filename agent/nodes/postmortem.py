"""Generate a structured post-mortem for tasks the agent failed to fix.

This is what separates honest engineering from demo-ware.
Every failed task gets a post-mortem; no silent failures.
"""

import logging
import os
from typing import Any

import anthropic

from agent.nodes.patch_generator import _compute_cost
from agent.state import AgentState

logger = logging.getLogger(__name__)

_LLM_PROMPT_TEMPLATE = """\
You are writing a technical post-mortem for an automated code repair system.

## Task that failed: {task_id}
## Iterations attempted: {iterations}
## Final test output:
{test_results}

## Patches tried:
{patch_history_numbered}

Analyze why the agent failed. Be specific and honest. Do not speculate \
beyond what the test output and patch history show.

Write the "Why It Failed" and "Root Cause Hypothesis" sections.
Label your hypothesis clearly as a hypothesis.
Keep each section under 100 words."""

_POSTMORTEM_TEMPLATE = """\
## Task: {task_id}

### What Was Attempted
- {n_patches} patch(es) tried over {iterations} iteration(s)
- Final status: `{status}`

### Approaches Tried
{approaches_list}

### Why It Failed
{why_failed}

### Root Cause Hypothesis
{root_cause}

### What a Human Engineer Would Do Next
1. Examine the full test suite to identify which invariants the patch was violating.
2. Add targeted print/logging statements to the failing code path to narrow root cause.
3. Search for related issues or open PRs in the upstream repository.

### Observability
| Metric | Value |
|---|---|
| Total tokens | {total_tokens:,} |
| Estimated cost | ${cost_usd:.4f} |
| LLM calls | {llm_calls} |
| Iterations | {iterations} |
"""


def _format_patch_history_numbered(patch_history: list[str]) -> str:
    """Format patch history with numbered entries for the LLM prompt."""
    if not patch_history:
        return "(no patches attempted)"
    return "\n\n".join(
        f"[Attempt {i}]\n{patch}" for i, patch in enumerate(patch_history, 1)
    )


def _approaches_list(patch_history: list[str]) -> str:
    """Summarise patch approaches as a bullet list (first line of each diff)."""
    if not patch_history:
        return "- (no patches generated)"
    items: list[str] = []
    for i, patch in enumerate(patch_history, 1):
        # Take first non-empty, non-header line as a summary
        for line in patch.splitlines():
            line = line.strip()
            if line and not line.startswith("---") and not line.startswith("+++"):
                items.append(f"- Attempt {i}: `{line[:80]}`")
                break
        else:
            items.append(f"- Attempt {i}: (empty diff)")
    return "\n".join(items)


def generate_postmortem(state: AgentState) -> dict[str, Any]:
    """Generate a structured markdown post-mortem for a failed task.

    Node function: receives full AgentState, returns partial update dict.

    Returns:
        {"postmortem": "...", "status": "failed"|"max_iterations", ...}
    """
    task_id = state["task_id"]
    iterations = state.get("iteration", 0)
    patch_history = state.get("patch_history", [])
    test_results = state.get("test_results") or "(no test output captured)"

    model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    logger.info("Generating post-mortem — task=%s iterations=%d", task_id, iterations)

    llm_prompt = _LLM_PROMPT_TEMPLATE.format(
        task_id=task_id,
        iterations=iterations,
        test_results=test_results[:3000],  # cap to avoid huge prompts
        patch_history_numbered=_format_patch_history_numbered(patch_history),
    )

    response = client.messages.create(
        model=model,
        max_tokens=768,
        temperature=0.2,
        messages=[{"role": "user", "content": llm_prompt}],
    )

    llm_analysis = response.content[0].text.strip()
    prompt_tokens = response.usage.input_tokens
    completion_tokens = response.usage.output_tokens

    # Parse LLM output into the two sections (best-effort)
    why_failed = llm_analysis
    root_cause = "(see 'Why It Failed' above)"
    lines = llm_analysis.splitlines()
    in_why = False
    in_root = False
    why_lines: list[str] = []
    root_lines: list[str] = []
    for line in lines:
        lower = line.lower()
        if "why it failed" in lower:
            in_why, in_root = True, False
            continue
        if "root cause" in lower:
            in_why, in_root = False, True
            continue
        if line.startswith("###") and in_root:
            break
        if in_why:
            why_lines.append(line)
        elif in_root:
            root_lines.append(line)

    if why_lines:
        why_failed = "\n".join(why_lines).strip()
    if root_lines:
        root_cause = "\n".join(root_lines).strip()

    token_usage = state.get("token_usage", {"prompt": 0, "completion": 0})
    total_tokens = token_usage.get("prompt", 0) + token_usage.get("completion", 0)

    postmortem = _POSTMORTEM_TEMPLATE.format(
        task_id=task_id,
        n_patches=len(patch_history),
        iterations=iterations,
        status=state.get("status", "failed"),
        approaches_list=_approaches_list(patch_history),
        why_failed=why_failed or "(analysis unavailable)",
        root_cause=root_cause or "(hypothesis unavailable)",
        total_tokens=total_tokens + prompt_tokens + completion_tokens,
        cost_usd=state.get("cost_usd", 0.0) + _compute_cost(model, prompt_tokens, completion_tokens),
        llm_calls=state.get("llm_calls", 0) + 1,
    )

    logger.info("Post-mortem generated for task %s (%d chars)", task_id, len(postmortem))

    # Accumulate token usage
    prior_usage = state.get("token_usage", {"prompt": 0, "completion": 0})
    new_usage = {
        "prompt": prior_usage.get("prompt", 0) + prompt_tokens,
        "completion": prior_usage.get("completion", 0) + completion_tokens,
    }
    prior_cost = state.get("cost_usd", 0.0)
    new_cost = prior_cost + _compute_cost(model, prompt_tokens, completion_tokens)

    return {
        "postmortem": postmortem,
        "status": state.get("status", "failed"),
        "token_usage": new_usage,
        "cost_usd": new_cost,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }
