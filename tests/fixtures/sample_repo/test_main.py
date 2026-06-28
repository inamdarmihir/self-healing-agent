"""Failing test fixture for the sample repo.

test_add FAILS because main.add returns a - b instead of a + b.
All other tests pass.
"""

import pytest

from main import add, divide, multiply


def test_add() -> None:
    """This test fails: add(2, 3) returns -1 instead of 5."""
    assert add(2, 3) == 5
    assert add(0, 0) == 0
    assert add(-1, 1) == 0


def test_multiply() -> None:
    assert multiply(2, 3) == 6
    assert multiply(0, 100) == 0
    assert multiply(-2, -3) == 6


def test_divide() -> None:
    assert divide(10.0, 2.0) == 5.0
    assert divide(0.0, 1.0) == 0.0


def test_divide_by_zero() -> None:
    with pytest.raises(ZeroDivisionError):
        divide(1.0, 0.0)
