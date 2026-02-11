"""BDD tests for sharing and export."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.sharing_steps import *

# Load scenarios from feature file
scenarios('sharing.feature')
