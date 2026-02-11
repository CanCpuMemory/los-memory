"""BDD tests for checkpoint management."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.checkpoint_steps import *

# Load scenarios from feature file
scenarios('checkpoints.feature')
