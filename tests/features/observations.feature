Feature: Observation Management
  As a user of the memory tool
  I want to create, search, edit and delete observations
  So that I can track my work and decisions

  Background:
    Given a new memory database

  Scenario: Add a simple observation
    When I add an observation with:
      | field    | value           |
      | title    | Test observation|
      | summary  | This is a test  |
      | project  | test-project    |
      | kind     | note            |
    Then the observation should be saved successfully
    And I should be able to retrieve it by ID

  Scenario: Search observations by keyword
    Given the following observations exist:
      | title            | summary                    | project   | kind     |
      | Database design  | Chose SQLite for storage   | backend   | decision |
      | API endpoint     | Added user login endpoint  | backend   | feature  |
      | Bug fix          | Fixed memory leak          | frontend  | fix      |
    When I search for "SQLite"
    Then I should find 1 observation
    And the observation title should be "Database design"

  Scenario: Edit an observation
    Given an observation exists with:
      | field    | value           |
      | title    | Original title  |
      | summary  | Original summary|
    When I edit the observation title to "Updated title"
    Then the observation should have title "Updated title"
    And the observation should still have summary "Original summary"

  Scenario: Delete an observation
    Given an observation exists with title "To be deleted"
    When I delete that observation
    Then the observation should not exist
    And searching for it should return no results

  Scenario: Auto-generate tags from content
    When I add an observation with:
      | title    | Performance optimization for database queries |
      | summary  | Added indexes to improve query speed         |
      | auto_tags| true                                         |
    Then the observation should have tags including "database" and "performance"
