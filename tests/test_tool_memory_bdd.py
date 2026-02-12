"""BDD tests for tool memory tracking."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.observation_steps import *
from steps.tool_memory_steps import *

# Load scenarios from feature file
scenarios('tool_memory.feature')
