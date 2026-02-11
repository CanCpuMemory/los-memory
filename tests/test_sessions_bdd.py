"""BDD tests for session management."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.session_steps import *

# Load scenarios from feature file
scenarios('sessions.feature')
