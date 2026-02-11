"""BDD tests for project management."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.project_steps import *

# Load scenarios from feature file
scenarios('projects.feature')
