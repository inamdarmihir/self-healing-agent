"""Root conftest — configures pytest collection for the whole project."""

collect_ignore_glob = [
    # The sample_repo fixture contains an intentionally failing test.
    # It is not part of the test suite; it's the agent's target.
    "tests/fixtures/sample_repo/*",
]
