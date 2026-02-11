Feature: Session Management
  As a user working on multiple tasks
  I want to group observations into sessions
  So that I can track work context and resume later

  Background:
    Given a new memory database
    And I am using the "codex" profile

  Scenario: Start a new session
    When I start a session with:
      | field       | value              |
      | project     | my-project         |
      | agent_type  | claude             |
      | summary     | Working on feature X |
    Then a session should be created
    And the session should be active
    And the session should have project "my-project"

  Scenario: Add observations to active session
    Given I have an active session for project "test-project"
    When I add an observation with title "First observation"
    Then the observation should belong to the active session
    When I add another observation with title "Second observation"
    Then both observations should be in the same session

  Scenario: End a session
    Given I have an active session with 3 observations
    When I end the session
    Then the session status should be "completed"
    And the session should have an end_time
    And there should be no active session

  Scenario: Resume a session
    Given a completed session with ID 1 exists
    When I resume session 1
    Then session 1 should be the active session

  Scenario: List sessions
    Given the following sessions exist:
      | project    | status    |
      | project-a  | active    |
      | project-b  | completed |
      | project-c  | completed |
    When I list all sessions
    Then I should see 3 sessions
    When I list only active sessions
    Then I should see 1 session
