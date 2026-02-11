Feature: Context Sharing and Export
  As a user collaborating with a team
  I want to export and import memory contexts
  So that I can share work with others

  Background:
    Given a new memory database

  Scenario: Export observations to JSON
    Given the following observations exist:
      | title    | project  | kind     |
      | Note 1   | proj-a   | note     |
      | Note 2   | proj-a   | decision |
    When I export to JSON file "/tmp/export.json"
    Then the file should exist
    And it should contain 2 observations
    And it should contain project "proj-a" metadata

  Scenario: Export to Markdown
    Given an observation exists with:
      | field    | value              |
      | title    | Test observation   |
      | summary  | This is the summary|
      | tags     | tag1,tag2          |
    When I export to Markdown file "/tmp/export.md"
    Then the file should contain "# Memory Context Bundle"
    And the file should contain "Test observation"
    And the file should contain "tag1"

  Scenario: Import bundle with dry-run
    Given a JSON bundle exists at "/tmp/test-bundle.json" with:
      | title    | project     |
      | Import 1 | new-project |
    When I import with dry-run
    Then no observations should be added
    And I should see preview of 1 observation to import

  Scenario: Import bundle
    Given a JSON bundle exists at "/tmp/test-bundle.json" with 2 observations
    When I import the bundle
    Then 2 observations should be added to the database
    And the sessions should be imported with new IDs

  Scenario: Filter export by project
    Given the following observations exist:
      | title    | project     |
      | Note A   | project-a   |
      | Note B   | project-b   |
    When I export with filter project="project-a"
    Then only 1 observation should be exported
    And the exported observation should be "Note A"
