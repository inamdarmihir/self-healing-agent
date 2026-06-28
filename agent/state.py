"""Single source of truth for all agent state. Never pass raw dicts between nodes."""

from typing import Literal, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # Input
    task_id: str
    repo_path: str
    failing_tests: list[str]  # list of test file paths
    issue_description: str

    # Context
    relevant_files: list[str]  # files identified by context_builder
    file_contents: dict[str, str]  # filename → content

    # Patch
    current_patch: Optional[str]  # unified diff string
    patch_history: list[str]  # all patches tried this session

    # Reflection
    reflection_critique: Optional[str]  # self-reflector output
    reflection_approved: bool  # whether to proceed or revise

    # Execution
    test_results: Optional[str]  # raw pytest output
    tests_passed: bool
    iteration: int
    max_iterations: int  # hard cap, default 5

    # Observability
    token_usage: dict[str, int]  # {"prompt": N, "completion": N}
    cost_usd: float
    llm_calls: int

    # Output
    status: Literal["running", "success", "failed", "max_iterations"]
    pr_url: Optional[str]
    postmortem: Optional[str]
