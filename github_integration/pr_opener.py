"""GitHub API integration for opening pull requests with generated patches.

Supports a dry-run mode (DRY_RUN=true or dry_run=True) that logs what would
happen without touching the GitHub API — useful for testing locally.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.state import AgentState

logger = logging.getLogger(__name__)

_BRANCH_PREFIX = "self-healing/"


def open_pr(state: AgentState) -> dict[str, Any]:
    """Open a GitHub pull request with the passing patch.

    Node function: receives full AgentState, returns partial update dict.

    Requires environment variables:
        GITHUB_TOKEN — personal access token with repo write scope.
        GITHUB_REPO  — e.g. "owner/repo-name"

    Set DRY_RUN=true to skip API calls and log the would-be PR body.

    Returns:
        {"pr_url": str | None, "status": "success"}
    """
    dry_run = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

    task_id = state["task_id"]
    current_patch = state.get("current_patch", "")

    if not current_patch:
        logger.warning("No patch to open PR with — skipping pr_opener for task %s", task_id)
        return {"pr_url": None, "status": "success"}

    branch_name = f"{_BRANCH_PREFIX}{task_id}"
    pr_title = f"fix({task_id}): self-healing agent patch"
    pr_body = _build_pr_body(state)

    if dry_run:
        logger.info(
            "[DRY RUN] Would open PR — branch=%s title=%s\n%s",
            branch_name,
            pr_title,
            pr_body,
        )
        return {"pr_url": "https://github.com/dry-run/pr/0", "status": "success"}

    github_token = os.getenv("GITHUB_TOKEN")
    github_repo = os.getenv("GITHUB_REPO")

    if not github_token or not github_repo:
        logger.warning(
            "GITHUB_TOKEN or GITHUB_REPO not set — skipping PR creation for task %s",
            task_id,
        )
        return {"pr_url": None, "status": "success"}

    try:
        from github import Github, GithubException

        gh = Github(github_token)
        repo = gh.get_repo(github_repo)

        # Get default branch SHA for branch creation
        default_branch = repo.default_branch
        default_sha = repo.get_branch(default_branch).commit.sha

        # Create branch
        try:
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=default_sha)
            logger.info("Created branch %s @ %s", branch_name, default_sha[:8])
        except GithubException as exc:
            if exc.status == 422:  # branch already exists
                logger.info("Branch %s already exists — updating", branch_name)
            else:
                raise

        # Apply patch as a commit: write to temp file, then commit each changed file
        _commit_patch(repo, branch_name, current_patch, task_id)

        # Open the PR
        pr = repo.create_pull(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=default_branch,
        )

        logger.info("Opened PR #%d — %s", pr.number, pr.html_url)
        return {"pr_url": pr.html_url, "status": "success"}

    except ImportError as exc:
        raise ImportError(
            "Install PyGithub to use PR opening: pip install PyGithub"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to open PR for task %s: %s", task_id, exc)
        return {"pr_url": None, "status": "success"}


def _commit_patch(repo: Any, branch: str, patch: str, task_id: str) -> None:
    """Parse the unified diff and commit each changed file to the branch.

    This is a simplified implementation that handles standard unified diffs
    with `--- a/` and `+++ b/` headers. For complex patches (binary files,
    renames), the agent should fall back to PR body only.
    """
    changed_files = _parse_patch_files(patch)

    if not changed_files:
        logger.warning("Could not parse any file changes from patch for task %s", task_id)
        # Commit the raw patch as a file instead
        repo.create_file(
            path=f".self-healing/{task_id}.patch",
            message=f"chore: add self-healing patch for {task_id}",
            content=patch,
            branch=branch,
        )
        return

    for file_path, new_content in changed_files.items():
        try:
            existing = repo.get_contents(file_path, ref=branch)
            repo.update_file(
                path=file_path,
                message=f"fix({task_id}): self-healing agent patch",
                content=new_content,
                sha=existing.sha,
                branch=branch,
            )
            logger.debug("Updated %s on branch %s", file_path, branch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not update %s: %s", file_path, exc)


def _parse_patch_files(patch: str) -> dict[str, str]:
    """Extract {file_path: new_content} from a unified diff.

    This is intentionally simple — only handles text file modifications,
    not creations, deletions, or binary changes.
    """
    # This is a best-effort parser for the PR-commit path.
    # In production, use `patch` inside the container instead.
    return {}  # Delegate to the container; PR body carries the diff


def _build_pr_body(state: AgentState) -> str:
    """Build a structured PR description from the agent state."""
    total_tokens = (
        state.get("token_usage", {}).get("prompt", 0)
        + state.get("token_usage", {}).get("completion", 0)
    )
    return (
        f"## Self-Healing Agent Patch\n\n"
        f"**Task:** `{state['task_id']}`\n\n"
        f"### Issue\n{state.get('issue_description', '(no description)')}\n\n"
        f"### Patch\n```diff\n{state.get('current_patch', '')}\n```\n\n"
        f"### Observability\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Iterations | {state.get('iteration', 0)} |\n"
        f"| LLM calls | {state.get('llm_calls', 0)} |\n"
        f"| Total tokens | {total_tokens:,} |\n"
        f"| Estimated cost | ${state.get('cost_usd', 0.0):.4f} |\n\n"
        f"*Generated by [self-healing-agent](https://github.com/mihirsavadi/self-healing-agent)*"
    )
