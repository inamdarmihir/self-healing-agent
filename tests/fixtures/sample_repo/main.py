"""Sample module used by unit test fixtures.

This file is intentionally broken so test_main.py has a failing test.
The agent's job is to produce a patch that makes test_main.py pass.
"""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a - b  # BUG: should be a + b


def multiply(a: int, b: int) -> int:
    """Return the product of two integers."""
    return a * b


def divide(a: float, b: float) -> float:
    """Return a divided by b.

    Raises:
        ZeroDivisionError: if b is zero.
    """
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b
