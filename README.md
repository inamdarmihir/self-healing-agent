# Self-Healing Code Agent

> Autonomous LangGraph agent that diagnoses failing tests, generates patches,
> executes them in an isolated Docker sandbox, and iterates — with a
> self-reflection loop and structured post-mortem on failure.

## Benchmark Results

| Metric | Value |
|---|---|
| **pass@1 on SWE-bench Lite** | **eval not yet run** (0/300 tasks) |
| Average iterations per task | — |
| Average cost per task | — |
| Cost per successful fix | — |
| Average time per task | — |

*Run `python scripts/run_swebench.py` to populate with real results.*
*Model: claude-sonnet-4-6. Full results in [`eval/benchmark_results.json`](eval/benchmark_results.json).*

## Architecture

```
              START
                │
        ┌───────▼────────┐
        │ context_builder │  tree-sitter import graph → relevant files
        └───────┬─────────┘
                │
        ┌───────▼────────┐
        │ patch_generator │  Claude claude-sonnet-4-6, temp=0.2, unified diff output
        └───────┬─────────┘
                │
        ┌───────▼────────┐
        │  self_reflector │  second LLM call: APPROVED / REVISE — [critique]
        └───────┬─────────┘
                │
      ┌─────────┴───────────┐
      │ not approved         │ approved (or iter >= 2)
      │ AND iter < 2         │
      └──► patch_generator   │
                             ▼
                    ┌────────────────┐
                    │ sandbox_executor│  Docker, net=none, 512MB, 60s timeout
                    └────────┬───────┘
                             │
            ┌────────────────┼──────────────────┐
            │ passed         │ failed            │ max_iterations
            ▼                ▼                   ▼
       ┌──────────┐  ┌────────────────┐  ┌────────────────────┐
       │ pr_opener│  │ patch_generator│  │ postmortem_generator│
       └────┬─────┘  └────────────────┘  └──────────┬─────────┘
            │                                        │
           END                                      END
```

## What Makes This Different

**Self-reflection loop:** Before any patch reaches the Docker sandbox, a second
LLM call critiques it for regression risk and root-cause validity. This runs on
iterations 0 and 1 only — past that, sandbox output is more informative than
another LLM opinion.

**Honest post-mortems:** Tasks the agent cannot fix get a structured failure
analysis: what was tried, why it failed, a root-cause hypothesis (clearly
labelled as a hypothesis), and three concrete next steps for a human engineer.

**Full observability:** Every task tracks token count, LLM calls, iteration
count, and USD cost. Aggregate metrics use the unbiased pass@k estimator from
Chen et al. 2021 (Codex paper).

**Docker sandbox:** The container has no network access, a 512 MB memory cap,
a 1 CPU quota, and a 60-second hard timeout. The repo is mounted read-only;
the container writes only to an in-container tmpdir.

## Failure Analysis

*Populate after running the evaluation. The three most common failure categories
on SWE-bench Lite for LLM-based agents are typically:*

1. **Multi-file changes required**: Agent modifies one file but the fix spans
   two or more interdependent modules — patch applies cleanly but tests still fail.
2. **Test infrastructure differences**: The failing test depends on external
   fixtures, database state, or network resources not available in the sandbox.
3. **Root cause in C extensions / compiled code**: The bug is in a C extension
   or Cython module; no Python-level patch can fix it.

*Update this section with real failure breakdowns after the evaluation run.*

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/your-handle/self-healing-agent.git
cd self-healing-agent
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY

# 3. Build the sandbox image
docker build -t self-healing-agent-sandbox:latest ./sandbox/

# 4. Run on a single task
python scripts/run_single_task.py \
    --task-id my-bug-001 \
    --repo-path /path/to/repo \
    --failing-tests tests/test_foo.py \
    --issue "The add() function returns the wrong value"
```

## Running the SWE-bench Evaluation

```bash
# Install extra deps
pip install datasets swebench

# Smoke test: first 5 tasks (~$0.10, ~5 minutes)
python scripts/run_swebench.py --limit 5

# Full evaluation: all 300 tasks (~$3–$10, ~3 hours depending on model)
python scripts/run_swebench.py

# Results are saved incrementally to eval/benchmark_results.json
# Safe to interrupt with Ctrl+C and inspect partial results
```

## Running Tests

```bash
pytest tests/ -v --cov=agent --cov=sandbox --cov=eval --cov-report=term-missing
```

Tests are fully mocked — no Anthropic API key or Docker daemon required.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `AGENT_MODEL` | No | `claude-sonnet-4-6` | Model override |
| `AGENT_MAX_ITERATIONS` | No | `5` | Hard cap per task |
| `GITHUB_TOKEN` | For PR opening | — | GitHub personal access token |
| `GITHUB_REPO` | For PR opening | — | `owner/repo` |
| `DRY_RUN` | No | `false` | Skip API calls and Docker |
| `LOG_LEVEL` | No | `INFO` | Python logging level |
| `RESULTS_DIR` | No | `./results` | Task result output directory |

## Project Structure

```
agent/nodes/       — one file per LangGraph node
agent/graph.py     — StateGraph wiring
agent/state.py     — single source of truth for all state
sandbox/           — Docker execution layer
eval/              — SWE-bench harness and metrics
github_integration/— PR creation via PyGithub
tests/             — pytest suite, >80% coverage target
scripts/           — CLI entrypoints
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a new agent node.
