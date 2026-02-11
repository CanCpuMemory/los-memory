"""BDD tests for observation management."""
from __future__ import annotations

from pytest_bdd import scenarios, given, when, then, parsers

# Import all step definitions
from steps.common_steps import *
from steps.observation_steps import *

# Load scenarios from feature file
scenarios('observations.feature')
