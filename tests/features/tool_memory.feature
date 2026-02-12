Feature: Tool Memory Tracking
  As an AI agent using the memory tool
  I want to track tool calls and their outcomes
  So that I can analyze usage patterns and get suggestions for tasks

  Background:
    Given a new memory database

  Scenario: Log a successful tool call
    When I log a tool call with:
      | field     | value                    |
      | tool      | search_files             |
      | input     | {"query": "TODO"}        |
      | output    | {"results": [1, 2, 3]}   |
      | status    | success                  |
      | duration  | 150                      |
    Then the tool call should be recorded successfully
    And the observation kind should be "tool_call"
    And the observation should have tag "tool"
    And the observation should have tag "search_files"

  Scenario: Log a tool call with error
    When I log a tool call with:
      | field     | value                      |
      | tool      | api_request                |
      | input     | {"url": "http://api.test"} |
      | output    | {"error": "Timeout"}       |
      | status    | error                      |
    Then the tool call should be recorded successfully
    And the observation should have tag "error"
    And the tool stats should show 1 error

  Scenario: View tool usage statistics
    Given the following tool calls have been logged:
      | tool          | status   | duration |
      | search_files  | success  | 100      |
      | search_files  | success  | 120      |
      | api_request   | success  | 200      |
      | api_request   | error    | 5000     |
    When I view tool statistics
    Then I should see 4 total calls
    And I should see 3 successful calls
    And I should see 1 error
    And "search_files" should have 2 calls
    And "api_request" should have 2 calls

  Scenario: Get tool suggestions for a task
    Given the following tool calls have been logged:
      | tool              | status  | summary                   |
      | search_files      | success | Searched for patterns     |
      | grep_search       | success | Grep for text             |
      | file_read         | success | Read file contents        |
      | database_query    | success | Query the database        |
    When I ask for tool suggestions for "find text in codebase"
    Then I should receive tool suggestions
    And "search_files" should be in the suggestions
    And "grep_search" should be in the suggestions

  Scenario: Filter tool stats by project
    Given the following tool calls have been logged:
      | tool      | project    | status  |
      | tool_a    | project-x  | success |
      | tool_b    | project-y  | success |
      | tool_c    | project-x  | success |
    When I view tool statistics for project "project-x"
    Then I should see 2 total calls
    And "tool_a" should be in the stats
    And "tool_c" should be in the stats
    And "tool_b" should not be in the stats
