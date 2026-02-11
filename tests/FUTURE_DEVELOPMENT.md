# Future Development Guide for los-memory

## BDD Test Roadmap

### Phase 1: Complete Current Test Coverage (Priority: High)

1. **Fix remaining test failures** (9 tests)
   - Checkpoint step definitions
   - Project statistics assertions
   - Auto-tag generation algorithm
   - Import bundle context initialization

2. **Add edge case tests**
   - Empty database operations
   - Very long text content
   - Special characters in tags/titles
   - Concurrent session handling

### Phase 2: Enhanced Test Coverage (Priority: Medium)

1. **Add integration tests**
   - Full workflow tests (session → observations → checkpoint → export)
   - Cross-profile data sharing
   - LLM hook integration

2. **Add performance tests**
   - Large database search performance
   - Bulk import/export
   - Timeline rendering with many observations

3. **Add error handling tests**
   - Invalid database paths
   - Corrupted data handling
   - Network failures (for LLM hooks)

### Phase 3: Advanced Features (Priority: Low)

1. **Multi-user scenario tests**
   - Concurrent access patterns
   - Data consistency checks

2. **Migration tests**
   - Schema upgrade tests
   - Data migration validation

## Code Quality Improvements

### Test Organization

1. **Group tests by feature area**
   ```
   tests/
   ├── unit/              # Unit tests for individual functions
   ├── integration/       # Integration tests
   ├── bdd/               # BDD tests
   │   ├── features/
   │   └── steps/
   └── performance/       # Performance benchmarks
   ```

2. **Add test markers**
   ```python
   @pytest.mark.slow          # Long-running tests
   @pytest.mark.critical      # Must-pass tests
   @pytest.mark.smoke         # Quick sanity tests
   ```

3. **Create test utilities**
   - Factory functions for test data
   - Common assertion helpers
   - Mock LLM hook for testing

### CI/CD Integration

1. **GitHub Actions workflow**
   ```yaml
   name: Tests
   on: [push, pull_request]
   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v3
         - uses: actions/setup-python@v4
           with:
             python-version: '3.10'
         - run: pip install pytest pytest-bdd
         - run: pytest tests/test_*_bdd.py -v
   ```

2. **Pre-commit hooks**
   - Run tests before commit
   - Check test coverage
   - Validate feature file syntax

## Best Practices for New Features

### When Adding New Features

1. **Start with BDD**
   - Write feature file first
   - Define scenarios with stakeholders
   - Get agreement on expected behavior

2. **Implement incrementally**
   - Write one scenario at a time
   - Run tests frequently
   - Fix failures immediately

3. **Document behavior**
   - Update feature descriptions
   - Add examples to docstrings
   - Update user documentation

### Feature File Template

```gherkin
Feature: [Feature Name]
  As a [user type]
  I want [capability]
  So that [benefit]

  Background:
    Given a new memory database
    And I am using the "codex" profile

  Scenario: [Happy path]
    When [action]
    Then [expected result]

  Scenario: [Error case]
    When [invalid action]
    Then [error message]

  Scenario: [Edge case]
    Given [special condition]
    When [action]
    Then [expected result]
```

### Step Definition Template

```python
"""Step definitions for [feature]."""
from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from conftest import BDDTestContext, parse_datatable, parse_datatable_rows, utc_now


@given("precondition")
def precondition(test_context: BDDTestContext):
    """Set up precondition."""
    pass


@when("action")
def action(test_context: BDDTestContext):
    """Perform action."""
    pass


@then("expected result")
def expected_result(test_context: BDDTestContext):
    """Verify expected result."""
    assert True
```

## Recommended Libraries

### Testing
- `pytest-bdd` - BDD testing framework
- `pytest-cov` - Coverage reporting
- `pytest-xdist` - Parallel test execution
- `factory-boy` - Test data generation
- `freezegun` - Time manipulation for tests

### Code Quality
- `black` - Code formatting
- `ruff` - Fast Python linter
- `mypy` - Static type checking
- `bandit` - Security analysis

## Monitoring Test Health

### Metrics to Track

1. **Test pass rate** - Target: >95%
2. **Test coverage** - Target: >80%
3. **Test execution time** - Target: <30 seconds
4. **Flaky test count** - Target: 0

### Dashboard Suggestions

Create a simple dashboard showing:
- Current test status
- Trend over time
- Slowest tests
- Most frequently failing tests

## Maintenance Schedule

### Weekly
- Review test failures
- Update broken tests
- Check for new edge cases

### Monthly
- Review test coverage
- Remove obsolete tests
- Refactor complex tests

### Quarterly
- Evaluate test framework updates
- Review and update test strategy
- Train team on BDD practices

## Resources

### Documentation
- [pytest-bdd documentation](https://pytest-bdd.readthedocs.io/)
- [Gherkin reference](https://cucumber.io/docs/gherkin/)
- [BDD best practices](https://cucumber.io/docs/bdd/)

### Tools
- VS Code extension: `Cucumber (Gherkin)`
- PyCharm: Built-in Gherkin support
- CLI: `behave` for Gherkin validation

## Contributing

When contributing new features:

1. Write BDD tests first
2. Ensure all tests pass
3. Update documentation
4. Follow code style guidelines
5. Add to CHANGELOG.md

## Questions?

See `BDD_TESTING_GUIDE.md` for usage instructions.
See `TROUBLESHOOTING.md` for common issues.
