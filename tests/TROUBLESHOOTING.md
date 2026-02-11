# BDD Test Troubleshooting Guide

## Current Test Failures and Fixes

### 1. test_autogenerate_tags_from_content

**Error**: `AssertionError: Expected tags database/performance not found in []`

**Cause**: The `auto_tags_from_text` function doesn't generate "database" or "performance" from the test text.

**Fix Options**:

Option A: Update the test expectation to match actual generated tags
```gherkin
# Change from:
Then the observation should have tags including "database" and "performance"
# To (example):
Then the observation should have tags including "optim" and "queri"
```

Option B: Improve `auto_tags_from_text` function in `memory_tool/utils.py`

### 2. test_import_bundle

**Error**: `AttributeError: 'BDDTestContext' object has no attribute 'last_export_path'`

**Fix**: Ensure `given_json_bundle_with_count` sets `last_export_path`:
```python
@test_context.last_export_path = filepath
```

### 3. Missing Step Definitions

Several tests are failing because step definitions referenced in feature files don't exist:

- `Given "a checkpoint exists with 3 observations"`
- `Then "I should see {count:d} checkpoints"`
- `Then "I should see project {project} with {count:d} observations"`

**Fix**: Add these step definitions to the appropriate files.

## pytest-bdd 8.x Migration Notes

### Major Changes from 7.x

1. **Datatable format**: Changed from list of dicts to list of lists
2. **Parameter injection**: `table` parameter replaced with `datatable`
3. **Step definition discovery**: Stricter matching rules

### Helper Functions

The `conftest.py` file provides helper functions for handling datatables:

```python
def parse_datatable(datatable: list[list[str]]) -> dict[str, str] | list[dict[str, str]]:
    """
    Parse a pytest-bdd 8.x datatable.

    For field/value tables:
        | field   | value  |
        | name    | test   |
    Returns: {"name": "test"}

    For header tables:
        | name  | age |
        | Alice | 25  |
    Returns: [{"name": "Alice", "age": "25"}]
    """

def parse_datatable_rows(datatable: list[list[str]]) -> list[dict[str, str]]:
    """
    Always returns list of dicts for header tables.
    """
```

## Common Errors

### fixture 'table' not found

**Cause**: Using `table` parameter name instead of `datatable`

**Fix**: Rename parameter from `table` to `datatable`

### StepDefinitionNotFoundError

**Cause**: Step text doesn't match exactly

**Fix**: Check for:
- Extra spaces
- Missing quotes
- Different capitalization
- Missing step definition import

### NameError for imports

**Cause**: Step file missing imports

**Fix**: Add to top of step file:
```python
from conftest import BDDTestContext, parse_datatable, parse_datatable_rows, utc_now
def tags_to_json(tags):
    from memory_tool.utils import tags_to_json as _ttj
    return _ttj(tags)
def tags_to_text(tags):
    from memory_tool.utils import tags_to_text as _ttt
    return _ttt(tags)
```
