"""Repository-wide pytest setup (loaded for every test directory)."""

import os

import pytest
from hypothesis import settings

# Reusable contract suites live in package modules, not test files; let pytest
# rewrite their asserts so failures show the values involved.
pytest.register_assert_rewrite("maq_brokers.testing")

# HYPOTHESIS_PROFILE=thorough runs the property tests with 100,000 examples each.
settings.register_profile("ci", max_examples=200, deadline=None)
settings.register_profile("thorough", max_examples=100_000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
