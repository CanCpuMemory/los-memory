"""BDD tests for observation links."""
from __future__ import annotations

from pytest_bdd import scenarios

# Import all step definitions
from steps.common_steps import *
from steps.observation_steps import *
from steps.observation_links_steps import *

# Load scenarios from feature file
scenarios('observation_links.feature')
