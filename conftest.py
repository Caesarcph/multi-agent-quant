"""Repository-wide pytest setup (loaded for every test directory)."""

import pytest

# Reusable contract suites live in package modules, not test files; let pytest
# rewrite their asserts so failures show the values involved.
pytest.register_assert_rewrite("maq_brokers.testing")
