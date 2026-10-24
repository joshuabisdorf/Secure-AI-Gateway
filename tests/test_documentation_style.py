"""Regression tests for Sphinx docstring style enforcement."""

import ast
from pathlib import Path

from scripts.style_check import _check_docstring, _docstring_fields


def _function_node(source: str) -> ast.FunctionDef:
    """Parse a single function used for style-checker regression tests."""
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def test_sphinx_fields_are_recognized_in_order() -> None:
    source = '''
def example(value: str) -> str:
    """Return the provided value.

    :param value: Input text.
    :returns: Original text.
    :raises ValueError: If the value is empty.

    .. note::

        - An empty value is rejected.
    """
    return value
'''
    node = _function_node(source)
    assert _docstring_fields(node) == [
        "param",
        "returns",
        "raises",
        "note",
    ]
    assert _check_docstring(Path("app/example.py"), node) == []


def test_google_style_heading_is_rejected() -> None:
    source = '''
def example(value: str) -> str:
    """Return the provided value.

    Args:
        value: Input text.
    """
    return value
'''
    violations = _check_docstring(
        Path("app/example.py"), _function_node(source)
    )
    assert len(violations) == 1
    assert violations[0].rule == "PY011"


def test_rmeio_heading_is_rejected() -> None:
    source = '''
def example(value: str) -> str:
    """Return the provided value.

    Requires:
        The input must be a string.
    """
    return value
'''
    violations = _check_docstring(
        Path("app/example.py"), _function_node(source)
    )
    assert len(violations) == 1
    assert violations[0].rule == "PY011"


def test_out_of_order_sphinx_fields_are_rejected() -> None:
    source = '''
def example(value: str) -> str:
    """Return the provided value.

    :returns: Original text.
    :param value: Input text.
    """
    return value
'''
    violations = _check_docstring(
        Path("app/example.py"), _function_node(source)
    )
    assert len(violations) == 1
    assert violations[0].rule == "PY007"


def test_duplicate_sphinx_return_field_is_rejected() -> None:
    source = '''
def example(value: str) -> str:
    """Return the provided value.

    :param value: Input text.
    :returns: Original text.
    :returns: A second return description.
    """
    return value
'''
    violations = _check_docstring(
        Path("app/example.py"), _function_node(source)
    )
    assert len(violations) == 1
    assert violations[0].rule == "PY007"


def test_missing_docstring_is_rejected() -> None:
    node = _function_node(
        "def example(value: str) -> str:\n"
        "    return value\n"
    )
    violations = _check_docstring(Path("app/example.py"), node)
    assert len(violations) == 1
    assert violations[0].rule == "PY006"
