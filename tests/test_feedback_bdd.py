"""BDD tests for natural language feedback."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.observation_steps import *
from steps.feedback_steps import *

# Load scenarios from feature file
scenarios('feedback.feature')
