Feature: Observation Links
  As a user of the memory tool
  I want to create links between related observations
  So that I can navigate and discover connected information

  Background:
    Given a new memory database

  Scenario: Create a link between observations
    Given an observation exists with title "API Design Decision"
    And another observation exists with title "API Implementation Notes"
    When I create a link from the first observation to the second with type "related"
    Then the link should be created successfully
    And the first observation should have 1 related observation

  Scenario: Find related observations
    Given an observation exists with title "Main Feature"
    And another observation exists with title "Sub-task A"
    And another observation exists with title "Sub-task B"
    And there is a link from "Main Feature" to "Sub-task A" with type "child"
    And there is a link from "Main Feature" to "Sub-task B" with type "child"
    When I find observations related to "Main Feature"
    Then I should find 2 related observations
    And "Sub-task A" should be in the related observations
    And "Sub-task B" should be in the related observations

  Scenario: Different link types
    Given an observation exists with title "Original Decision"
    And another observation exists with title "Refined Decision"
    When I create a link from "Refined Decision" to "Original Decision" with type "refines"
    Then the link type should be "refines"

  Scenario: Remove a link
    Given an observation exists with title "Parent"
    And another observation exists with title "Child"
    And there is a link from "Parent" to "Child" with type "child"
    When I remove the link from "Parent" to "Child"
    Then the link should be removed
    And "Parent" should have 0 related observations

  Scenario: Suggest related observations by similarity
    Given an observation exists with:
      | field   | value                            |
      | title   | Database Optimization            |
      | summary | Optimizing queries for performance |
      | tags    | database, performance            |
    And another observation exists with:
      | field   | value                            |
      | title   | Query Performance Tuning         |
      | summary | Database query improvements        |
      | tags    | database, performance            |
    When I find similar observations to the first observation
    Then "Query Performance Tuning" should be in the suggestions
