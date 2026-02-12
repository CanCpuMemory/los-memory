Feature: Natural Language Feedback
  As a user of the memory tool
  I want to provide natural language feedback to correct or supplement observations
  So that I can keep my memory accurate and up-to-date

  Background:
    Given a new memory database

  Scenario: Correct an observation with feedback
    Given an observation exists with:
      | field   | value              |
      | title   | API key for service X |
      | summary | The key is abc123  |
    When I provide feedback "修正: The key is xyz789 not abc123" on that observation
    Then the observation should have summary "The key is xyz789 not abc123"
    And the feedback should be recorded with action "correct"

  Scenario: Supplement an observation with additional info
    Given an observation exists with:
      | field   | value              |
      | title   | Project setup      |
      | summary | Initial setup done |
    When I provide feedback "补充: Also need to configure the database" on that observation
    Then the observation summary should contain "[补充] Also need to configure the database"
    And the feedback should be recorded with action "supplement"

  Scenario: Delete an observation via feedback
    Given an observation exists with title "To be deleted"
    When I provide feedback "删除" on that observation
    Then the observation should not exist
    And the feedback should be recorded with action "delete"

  Scenario: Preview feedback changes with dry-run
    Given an observation exists with:
      | field   | value              |
      | title   | Original title     |
      | summary | Original summary   |
    When I preview feedback "修正: summary to Updated summary" on that observation
    Then the observation should still have summary "Original summary"

  Scenario: View feedback history
    Given an observation exists with:
      | field   | value              |
      | title   | Test observation   |
      | summary | Original content   |
    And I have provided feedback "补充: First addition" on that observation
    And I have provided feedback "补充: Second addition" on that observation
    When I view feedback history for that observation
    Then I should see 2 feedback entries
    And the latest feedback should contain "Second addition"

  Scenario: Correct observation title explicitly
    Given an observation exists with:
      | field   | value              |
      | title   | Old title          |
      | summary | Some summary       |
    When I provide feedback "修正: title: New title" on that observation
    Then the observation should have title "New title"
    And the observation should still have summary "Some summary"

  Scenario: Handle unknown feedback gracefully
    Given an observation exists with:
      | field   | value              |
      | title   | Test observation   |
      | summary | Test summary       |
    When I provide feedback "something unclear" on that observation
    Then the observation summary should contain "[补充] something unclear"
