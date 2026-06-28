"""Generate a unified diff patch to fix the failing tests.

Prompt engineering is an engineering artifact here — prompts are pinned constants,
not runtime strings. Temperature is 0.2 for near-deterministic repairs.
"""

import logging
import os
from typing import Any

import anthropic

from agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing constants (USD per token) — update when Anthropic changes pricing
# ---------------------------------------------------------------------------
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"prompt": 3.00 / 1_000_000, "completion": 15.00 / 1_000_000},
    "claude-3-5-sonnet-20241022": {"prompt": 3.00 / 1_000_000, "completion": 15.00 / 1_000_000},
    "claude-3-haiku-20240307": {"prompt": 0.25 / 1_000_000, "completion": 1.25 / 1_000_000},
}

_SYSTEM_PROMPT = """\
You are a senior Python software engineer performing a precise bug fix.
You will be given:
1. A description of the bug or failing behavior
2. The content of files relevant to the failure
3. The pytest output showing exactly which assertions fail
4. A history of patches that were already tried (do not repeat these)

Your task: produce a minimal unified diff (patch -p1 format) that fixes \
the failing tests without breaking any passing tests.

Output format rules — follow exactly:
- Output ONLY the unified diff. No explanation, no markdown, no prose.
- The diff must be syntactically valid and applicable with `patch -p1`
- Do not add new dependencies
- Do not refactor code unrelated to the bug
- If you cannot produce a valid patch, output exactly: CANNOT_FIX — [reason]"""

_USER_TEMPLATE = """\
## Issue Description
{issue_description}

## Failing Test Output
```
{test_results}
```

## Relevant Files
{file_contents_block}

## Patch History (do not repeat these approaches)
{patch_history_block}

## Your patch (unified diff only):"""


def _format_file_contents(file_contents: dict[str, str]) -> str:
    """Format file contents for the prompt."""
    if not file_contents:
        return "(no files available)"
    parts: list[str] = []
    for filename, content in file_contents.items():
        parts.append(f"### {filename}\n```python\n{content}\n```")
    return "\n\n".join(parts)


def _format_patch_history(patch_history: list[str]) -> str:
    """Format patch history for the prompt."""
    if not patch_history:
        return "(no previous patches)"
    parts: list[str] = []
    for i, patch in enumerate(patch_history, 1):
        parts.append(f"### Attempt {i}\n```diff\n{patch}\n```")
    return "\n\n".join(parts)


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return USD cost for a single LLM call."""
    pricing = _MODEL_PRICING.get(model, _MODEL_PRICING["claude-sonnet-4-6"])
    return prompt_tokens * pricing["prompt"] + completion_tokens * pricing["completion"]


def generate_patch(state: AgentState) -> dict[str, Any]:
    """Call the LLM to generate a unified diff patch.

    Node function: receives full AgentState, returns partial update dict.

    Returns:
        {
            "current_patch": str,
            "patch_history": updated list,
            "token_usage": accumulated dict,
            "cost_usd": accumulated float,
            "llm_calls": incremented int,
        }
    """
    model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    user_prompt = _USER_TEMPLATE.format(
        issue_description=state["issue_description"] or "(no description provided)",
        test_results=state.get("test_results") or "(no test output yet — first attempt)",
        file_contents_block=_format_file_contents(state.get("file_contents", {})),
        patch_history_block=_format_patch_history(state.get("patch_history", [])),
    )

    logger.info(
        "Generating patch — task=%s iteration=%d model=%s",
        state["task_id"],
        state.get("iteration", 0),
        model,
    )

    if state.get("reflection_critique"):
        user_prompt = (
            f"## Self-Reflection Critique (address this first)\n"
            f"{state['reflection_critique']}\n\n" + user_prompt
        )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        temperature=0.2,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_patch = response.content[0].text.strip()
    prompt_tokens = response.usage.input_tokens
    completion_tokens = response.usage.output_tokens

    logger.info(
        "Patch generated — %d prompt tokens, %d completion tokens",
        prompt_tokens,
        completion_tokens,
    )

    if raw_patch.startswith("CANNOT_FIX"):
        logger.warning("LLM reported CANNOT_FIX: %s", raw_patch)

    # Accumulate token usage (never reset)
    prior_usage = state.get("token_usage", {"prompt": 0, "completion": 0})
    new_usage = {
        "prompt": prior_usage.get("prompt", 0) + prompt_tokens,
        "completion": prior_usage.get("completion", 0) + completion_tokens,
    }

    prior_cost = state.get("cost_usd", 0.0)
    new_cost = prior_cost + _compute_cost(model, prompt_tokens, completion_tokens)

    # Append patch to history if non-empty and not a CANNOT_FIX
    patch_history = list(state.get("patch_history", []))
    if raw_patch and not raw_patch.startswith("CANNOT_FIX"):
        patch_history.append(raw_patch)

    return {
        "current_patch": raw_patch,
        "patch_history": patch_history,
        "token_usage": new_usage,
        "cost_usd": new_cost,
        "llm_calls": state.get("llm_calls", 0) + 1,
        "reflection_critique": None,  # clear critique so reflector re-evaluates fresh
        "reflection_approved": False,
    }
