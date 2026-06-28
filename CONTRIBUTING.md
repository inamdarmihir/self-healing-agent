# Contributing

## Adding a New Agent Node

A node is a Python function that takes the full `AgentState` and returns
a `dict` of state updates. LangGraph merges the returned dict into the current
state — only return keys you're actually changing.

### 1. Create the node file

```
agent/nodes/my_new_node.py
```

Skeleton:

```python
"""One-sentence description of what this node does."""

import logging
from typing import Any

from agent.state import AgentState

logger = logging.getLogger(__name__)


def my_new_node(state: AgentState) -> dict[str, Any]:
    """Docstring: what this node does, what it reads, what it writes.

    Returns:
        Partial AgentState update dict.
    """
    # Read from state
    task_id = state["task_id"]

    # Do work
    result = ...

    logger.info("my_new_node finished — task=%s", task_id)

    # Return only the keys you changed
    return {"some_field": result}
```

Rules:
- **Type-annotate every parameter and return type.**
- **Use `logging`, not `print`.** Messages should include `task_id`.
- **No bare `except:`** — always catch specific exceptions.
- **Never mutate `state` in place** — only return updates.
- **Track token usage** if you make an LLM call. Accumulate into
  `state["token_usage"]`, never reset it.

### 2. Wire it into the graph

Open `agent/graph.py`:

```python
from agent.nodes.my_new_node import my_new_node

workflow.add_node("my_new_node", my_new_node)
workflow.add_edge("previous_node", "my_new_node")
workflow.add_edge("my_new_node", "next_node")
```

If your node is a conditional branch point, add it to `agent/edges.py`:

```python
def route_after_my_node(state: AgentState) -> str:
    if state["some_condition"]:
        return "branch_a"
    return "branch_b"
```

Then in `graph.py`:

```python
workflow.add_conditional_edges(
    "my_new_node",
    route_after_my_node,
    {"branch_a": "node_a", "branch_b": "node_b"},
)
```

### 3. Add tests

Create `tests/test_my_new_node.py`. Mock all external calls (LLM, Docker, GitHub API).
Aim for:
- Happy path with valid input
- Graceful handling of empty/None state fields
- Token accumulation correctness if LLM is involved
- At least one edge-case test

```python
from unittest.mock import MagicMock, patch
from agent.nodes.my_new_node import my_new_node

def test_my_node_happy_path() -> None:
    state = {...}  # build minimal state
    result = my_new_node(state)
    assert "some_field" in result
```

### 4. Quality gates

Before opening a PR:

```bash
ruff check .
mypy agent/ eval/ sandbox/
pytest tests/ --cov=agent --cov=sandbox --cov=eval --cov-report=term-missing
```

All three must pass with zero errors.

## Project Layout

```
agent/nodes/       — one file per node; keep nodes focused and single-purpose
agent/edges.py     — all conditional routing logic lives here
agent/graph.py     — only wiring; no business logic
agent/state.py     — the single source of truth for all state fields
sandbox/           — Docker execution layer; never import from agent/
eval/              — benchmarking; independent of agent implementation details
tests/             — mirrors the source tree; fixtures in tests/fixtures/
```

## Code Style

- `ruff` for formatting and linting (`line-length = 88`)
- `mypy --strict` for type checking
- Docstrings on every public function
- No `TODO` or `FIXME` in committed code — open an issue instead
