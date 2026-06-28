"""Critique the generated patch before applying it.

The self-reflection loop is what separates a toy agent from a production one.
It adds ~$0.002 per task but prevents wasted sandbox executions on bad patches.
Only fires on iterations 0 and 1 — not worth the cost on later retries.
"""

import logging
import os
from pathlib import Path
from typing import Any

import anthropic

from agent.nodes.patch_generator import _compute_cost
from agent.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a principal engineer reviewing a code patch before it goes to CI.
Your job is to catch bugs in the patch itself, not just verify it looks plausible.
Be skeptical. Most patches that reach you have subtle issues."""

_USER_TEMPLATE = """\
## Patch to Review
```diff
{current_patch}
```

## Failing Tests (what this patch must fix)
```python
{failing_test_content}
```

## Review Checklist
Answer each briefly:
1. Root cause: Does this fix the actual root cause, or mask a symptom?
2. Regression risk: Could this break any passing tests?
3. Syntax validity: Is this a syntactically valid Python change?
4. Edge cases: Are there inputs that would still fail after this patch?

## Verdict
If all concerns are resolved: respond with exactly "APPROVED"
If any concern is unresolved: respond with "REVISE — " followed by \
your single most important concern in one sentence."""


def _load_test_content(state: AgentState) -> str:
    """Load the content of failing test files for the reflector prompt."""
    parts: list[str] = []
    repo_path = state.get("repo_path", "")
    file_contents = state.get("file_contents", {})

    for test_path_str in state.get("failing_tests", []):
        # Check already-loaded file_contents first
        if test_path_str in file_contents:
            parts.append(f"# {test_path_str}\n{file_contents[test_path_str]}")
            continue

        # Fallback: read from disk
        test_path = Path(test_path_str)
        if not test_path.is_absolute() and repo_path:
            test_path = Path(repo_path) / test_path_str

        try:
            parts.append(f"# {test_path_str}\n{test_path.read_text(encoding='utf-8')}")
        except OSError as exc:
            logger.warning("Cannot read test file %s: %s", test_path_str, exc)

    return "\n\n".join(parts) if parts else "(test content unavailable)"


def reflect_on_patch(state: AgentState) -> dict[str, Any]:
    """Critique the current patch using a second LLM call.

    Node function: receives full AgentState, returns partial update dict.

    Reflection is skipped (auto-approved) when iteration >= 2 to avoid
    burning tokens on retries where the sandbox output is more informative.

    Returns:
        {
            "reflection_critique": str | None,
            "reflection_approved": bool,
            "token_usage": accumulated dict,
            "cost_usd": accumulated float,
            "llm_calls": incremented int,
        }
    """
    iteration = state.get("iteration", 0)

    # Skip reflection on later iterations — sandbox output is more informative
    if iteration >= 2:
        logger.info(
            "Skipping reflection on iteration %d (cost control) — auto-approved",
            iteration,
        )
        return {
            "reflection_critique": None,
            "reflection_approved": True,
        }

    current_patch = state.get("current_patch", "")
    if not current_patch or current_patch.startswith("CANNOT_FIX"):
        logger.info("No valid patch to reflect on — auto-approving to trigger postmortem path")
        return {
            "reflection_critique": None,
            "reflection_approved": True,
        }

    model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    user_prompt = _USER_TEMPLATE.format(
        current_patch=current_patch,
        failing_test_content=_load_test_content(state),
    )

    logger.info(
        "Reflecting on patch — task=%s iteration=%d model=%s",
        state["task_id"],
        iteration,
        model,
    )

    response = client.messages.create(
        model=model,
        max_tokens=512,
        temperature=0.1,  # More deterministic for critique
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    verdict_text = response.content[0].text.strip()
    prompt_tokens = response.usage.input_tokens
    completion_tokens = response.usage.output_tokens

    approved = verdict_text.startswith("APPROVED")
    critique = None if approved else verdict_text

    logger.info(
        "Reflection verdict: %s — task=%s iteration=%d (%d tokens)",
        "APPROVED" if approved else "REVISE",
        state["task_id"],
        iteration,
        prompt_tokens + completion_tokens,
    )

    if not approved:
        logger.info("Critique: %s", critique)

    # Accumulate token usage
    prior_usage = state.get("token_usage", {"prompt": 0, "completion": 0})
    new_usage = {
        "prompt": prior_usage.get("prompt", 0) + prompt_tokens,
        "completion": prior_usage.get("completion", 0) + completion_tokens,
    }
    prior_cost = state.get("cost_usd", 0.0)
    new_cost = prior_cost + _compute_cost(model, prompt_tokens, completion_tokens)

    return {
        "reflection_critique": critique,
        "reflection_approved": approved,
        "token_usage": new_usage,
        "cost_usd": new_cost,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }
