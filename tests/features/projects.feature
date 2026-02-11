Feature: Project Management
  As a user working on multiple projects
  I want to organize observations by project
  So that I can maintain separate contexts

  Background:
    Given a new memory database

  Scenario: Set active project
    When I set the active project to "my-awesome-project"
    Then the active project should be "my-awesome-project"

  Scenario: Auto-assign observations to active project
    Given the active project is "default-project"
    When I add an observation with:
      | field    | value         |
      | title    | Test note     |
      | summary  | A test note   |
    Then the observation should belong to project "default-project"

  Scenario: List projects with statistics
    Given the following observations exist:
      | title    | project     |
      | Note 1   | project-a   |
      | Note 2   | project-a   |
      | Note 3   | project-b   |
    When I list projects
    Then I should see project "project-a" with 2 observations
    And I should see project "project-b" with 1 observation

  Scenario: Archive a project
    Given observations exist for project "old-project"
    When I archive project "old-project"
    Then all observations should be moved to "archived/old-project"
    And the original project should have no observations

  Scenario: Get project statistics
    Given the following observations exist in project "stats-test":
      | title    | kind      | tags      |
      | Note 1   | note      | tag1,tag2 |
      | Note 2   | decision  | tag2,tag3 |
      | Note 3   | fix       | tag1      |
    When I get statistics for project "stats-test"
    Then I should see 3 total observations
    And I should see 3 different kinds
    And I should see top tags including "tag1" and "tag2"
