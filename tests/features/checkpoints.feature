Feature: Checkpoint Management
  As a user working on long-running tasks
  I want to save checkpoints at important milestones
  So that I can resume work from a known state

  Background:
    Given a new memory database
    And I am using the "codex" profile

  Scenario: Create a checkpoint
    Given I have an active session with 5 observations
    When I create a checkpoint named "milestone-1" with tag "milestone"
    Then the checkpoint should be created successfully
    And the checkpoint should reference the current session
    And the checkpoint should record 5 observations

  Scenario: List checkpoints
    Given the following checkpoints exist:
      | name          | tag       |
      | v1.0-release  | release   |
      | sprint-5-end  | sprint    |
      | bug-fix-done  | milestone |
    When I list checkpoints
    Then I should see 3 checkpoints
    When I list checkpoints with tag "release"
    Then I should see 1 checkpoint

  Scenario: Resume from checkpoint
    Given a checkpoint exists with:
      | field       | value              |
      | name        | feature-complete   |
      | project     | my-project         |
      | session_id  | 1                  |
    When I resume from checkpoint 1
    Then the active project should be "my-project"
    And session 1 should be the active session
    And I should see recent observations from the checkpoint

  Scenario: Show checkpoint details
    Given a checkpoint exists with 3 observations
    When I show checkpoint details
    Then I should see the checkpoint metadata
    And I should see all 3 observations
