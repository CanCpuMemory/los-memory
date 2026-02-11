# BDD Testing Guide for los-memory

## Overview

This project uses Behavior-Driven Development (BDD) testing with pytest-bdd to ensure all features work correctly. The tests are written in Gherkin syntax (`.feature` files) and mapped to Python step definitions.

## Test Structure

```
tests/
├── features/                 # Gherkin feature files
│   ├── observations.feature  # Observation CRUD tests
│   ├── sessions.feature      # Session management tests
│   ├── projects.feature      # Project management tests
│   ├── checkpoints.feature   # Checkpoint/resumption tests
│   └── sharing.feature       # Export/import tests
├── steps/                    # Step definitions
│   ├── common_steps.py       # Shared step definitions
│   ├── observation_steps.py  # Observation-specific steps
│   ├── session_steps.py      # Session-specific steps
│   ├── project_steps.py      # Project-specific steps
│   ├── checkpoint_steps.py   # Checkpoint-specific steps
│   └── sharing_steps.py      # Sharing-specific steps
├── conftest.py               # Pytest fixtures and helpers
├── test_*_bdd.py             # BDD test runners
└── BDD_TESTING_GUIDE.md      # This guide
```

## Running Tests

### Run All BDD Tests
```bash
python3.10 -m pytest tests/test_*_bdd.py -v
```

### Run Specific Feature Tests
```bash
python3.10 -m pytest tests/test_observations_bdd.py -v
python3.10 -m pytest tests/test_sessions_bdd.py -v
python3.10 -m pytest tests/test_projects_bdd.py -v
python3.10 -m pytest tests/test_checkpoints_bdd.py -v
python3.10 -m pytest tests/test_sharing_bdd.py -v
```

### Run Single Test
```bash
python3.10 -m pytest tests/test_observations_bdd.py::test_add_a_simple_observation -v
```

### Run with Coverage
```bash
python3.10 -m pytest tests/test_*_bdd.py --cov=memory_tool --cov-report=html
```

## Current Test Status

| Feature | Tests | Status |
|---------|-------|--------|
| Observations | 5 | 4 passing, 1 pending |
| Sessions | 5 | All passing |
| Projects | 5 | 2 passing, 3 pending |
| Checkpoints | 4 | Pending fixes |
| Sharing | 5 | 4 passing, 1 pending |

**Total: 24 tests, 15 passing, 9 pending fixes**

## Writing New BDD Tests

### 1. Create Feature File

Create a `.feature` file in `tests/features/`:

```gherkin
Feature: New Feature Description
  As a [role]
  I want [capability]
  So that [benefit]

  Background:
    Given a new memory database

  Scenario: Specific scenario description
    When I perform some action
    Then I should see expected result
```

### 2. Create Step Definitions

Add steps to appropriate file in `tests/steps/`:

```python
from pytest_bdd import given, when, then, parsers
from conftest import BDDTestContext

@when(parsers.parse('I perform some action'))
def perform_action(test_context: BDDTestContext):
    """Step implementation."""
    # Implementation here
    pass

@then(parsers.parse('I should see expected result'))
def check_result(test_context: BDDTestContext):
    """Verify result."""
    assert test_context.some_value == expected
```

### 3. Create Test Runner

Create `test_feature_bdd.py`:

```python
"""BDD tests for new feature."""
from pytest_bdd import scenarios

# Import step definitions
from steps.common_steps import *
from steps.new_feature_steps import *

# Load scenarios
scenarios('features/new_feature.feature')
```

## Key Conventions

### Datatable Handling (pytest-bdd 8.x)

pytest-bdd 8.x passes datatables as `list[list[str]]`, not as dictionaries:

```python
# Helper functions from conftest.py
def parse_datatable(datatable: list[list[str]]) -> dict[str, str]:
    """Convert field/value table to dict."""
    # Returns dict for field/value tables
    # Returns list of dicts for header tables

def parse_datatable_rows(datatable: list[list[str]]) -> list[dict[str, str]]:
    """Convert table to list of dicts."""
    # Always returns list of dicts

# Usage
@when("I add an observation with:")
def add_observation(test_context: BDDTestContext, datatable):
    data = parse_datatable(datatable)
    if isinstance(data, list):
        data = data[0] if data else {}
    # Use data dict
```

### Test Context

Use `BDDTestContext` from `conftest.py` to share state:

```python
class BDDTestContext:
    db_path: Path | None
    conn: sqlite3.Connection
    profile: str
    last_observation_id: int | None
    last_session_id: int | None
    last_checkpoint_id: int | None
    last_export_path: str | None
    search_results: list
```

### Database Setup

Each test gets a fresh temporary database automatically:

```python
@pytest.fixture
def test_context():
    """Create a fresh test context with temporary database."""
    ctx = BDDTestContext()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        ctx.db_path = Path(f.name)
    ctx.conn = connect_db(str(ctx.db_path))
    ensure_schema(ctx.conn)
    ensure_fts(ctx.conn)
    yield ctx
    # Cleanup after test
```

## Troubleshooting

### Step Definition Not Found

Error: `Step definition is not found: Given "..."`

**Cause**: Missing step definition or typo

**Fix**:
1. Check exact step text matches
2. Ensure step file is imported in test runner
3. Check `pytest.ini` has correct `bdd_features_base_dir`

### Datatable Issues

Error: `'list' object has no attribute 'get'`

**Cause**: Using old pytest-bdd table syntax

**Fix**: Use `parse_datatable()` or `parse_datatable_rows()` helpers

### Import Errors

Error: `NameError: name 'utc_now' is not defined`

**Cause**: Missing import in step file

**Fix**: Add imports:
```python
from conftest import BDDTestContext, parse_datatable, parse_datatable_rows, utc_now
def tags_to_json(tags): from memory_tool.utils import tags_to_json as _ttj; return _ttj(tags)
def tags_to_text(tags): from memory_tool.utils import tags_to_text as _ttt; return _ttt(tags)
```

## Best Practices

1. **Keep step definitions small** - Each step should do one thing
2. **Reuse existing steps** - Don't duplicate step definitions
3. **Use the test context** - Store state in `test_context`, not global variables
4. **Clean up resources** - The fixture handles DB cleanup automatically
5. **Use descriptive names** - Step names should clearly describe the action
6. **Add docstrings** - All step functions should have docstrings
7. **Test one thing at a time** - Each scenario should verify one behavior
8. **Use Background wisely** - Only for setup needed by all scenarios

## Maintenance Tips

1. **Run tests before commits** - Ensure changes don't break existing tests
2. **Update tests with code changes** - Keep tests in sync with implementation
3. **Add tests for bugs** - Write tests that reproduce reported bugs
4. **Review test coverage** - Aim for high coverage of critical paths
5. **Document complex scenarios** - Add comments for non-obvious test logic

## Next Steps for Test Completion

To fix remaining test failures:

1. **Checkpoint tests**: Add missing step definitions
2. **Project tests**: Fix datatable parsing for project statistics
3. **Auto-tag test**: Update tag generation algorithm
4. **Import test**: Initialize `last_export_path` in test context

See `TROUBLESHOOTING.md` for detailed fixes.
