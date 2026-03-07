# los-memory Multi-Language SDK Design

## Overview

This document defines the SDK interfaces for Go, Rust, and Node.js. All SDKs wrap the los-memory CLI and parse JSON output, providing idiomatic APIs for each language.

## Common Design Principles

### 1. CLI Wrapper Architecture

All SDKs follow the same wrapper pattern:

```
SDK Method -> Build CLI Command -> Execute -> Parse JSON -> Return Typed Result
```

### 2. Error Handling Strategy

- Parse JSON error responses into language-specific exceptions/errors
- Include error code, message, and suggestion
- Provide original error context for debugging

### 3. Configuration

SDKs support configuration via:
- Environment variables (MEMORY_PROFILE, MEMORY_DB_PATH)
- Constructor parameters
- Configuration files (where idiomatic)

### 4. Response Types

All SDKs implement equivalent response types:
- `Observation`
- `Session`
- `Checkpoint`
- `PaginatedResult<T>`
- `SearchResult`

---

## Go SDK Design

### Package Structure

```
losmemory/
├── client.go          # Main client
├── models.go          # Data models
├── errors.go          # Error types
├── options.go         # Functional options
├── cmd/
│   └── losmemory/     # CLI wrapper
└── internal/
    └── jsonutil/      # JSON utilities
```

### Installation

```bash
go get github.com/los/memory/sdk/go
```

### Error Types

```go
// errors.go
package losmemory

import "fmt"

// ErrorCode represents error categories
type ErrorCode string

const (
    ErrValidation     ErrorCode = "VAL_INVALID_INPUT"
    ErrNotFound       ErrorCode = "NF_OBSERVATION"
    ErrDatabase       ErrorCode = "DB_ERROR"
    ErrSchemaVersion  ErrorCode = "DB_SCHEMA_VERSION"
    ErrPermission     ErrorCode = "SYS_PERMISSION"
    ErrConflict       ErrorCode = "CONF_DUPLICATE"
)

// MemoryError is the base error type
type MemoryError struct {
    Code       ErrorCode
    Message    string
    Category   string
    Details    map[string]interface{}
    Suggestion string
}

func (e *MemoryError) Error() string {
    return fmt.Sprintf("[%s] %s", e.Code, e.Message)
}

// IsNotFound checks if error is not found
func IsNotFound(err error) bool {
    if e, ok := err.(*MemoryError); ok {
        return e.Code == ErrNotFound
    }
    return false
}
```

### Models

```go
// models.go
package losmemory

import "time"

// ObservationKind represents observation types
type ObservationKind string

const (
    KindNote     ObservationKind = "note"
    KindDecision ObservationKind = "decision"
    KindFix      ObservationKind = "fix"
    KindIncident ObservationKind = "incident"
)

// Observation represents a memory observation
type Observation struct {
    ID        int64           `json:"id"`
    Timestamp time.Time       `json:"timestamp"`
    Project   string          `json:"project"`
    Kind      ObservationKind `json:"kind"`
    Title     string          `json:"title"`
    Summary   string          `json:"summary"`
    Tags      []string        `json:"tags"`
    Raw       string          `json:"raw"`
    SessionID *int64          `json:"session_id,omitempty"`
}

// Session represents a work session
type Session struct {
    ID         int64      `json:"id"`
    StartTime  time.Time  `json:"start_time"`
    EndTime    *time.Time `json:"end_time,omitempty"`
    Project    string     `json:"project"`
    WorkingDir string     `json:"working_dir"`
    AgentType  string     `json:"agent_type"`
    Summary    string     `json:"summary"`
    Status     string     `json:"status"`
}

// Checkpoint represents a project checkpoint
type Checkpoint struct {
    ID               int64      `json:"id"`
    Timestamp        time.Time  `json:"timestamp"`
    Name             string     `json:"name"`
    Description      string     `json:"description"`
    Tag              string     `json:"tag"`
    SessionID        *int64     `json:"session_id,omitempty"`
    ObservationCount int        `json:"observation_count"`
    Project          string     `json:"project"`
}

// SearchResult includes relevance score
type SearchResult struct {
    Observation Observation `json:"observation"`
    Score       *float64    `json:"score,omitempty"`
}

// PaginatedResult wraps paginated responses
type PaginatedResult[T any] struct {
    Items    []T  `json:"items"`
    Total    int  `json:"total"`
    Limit    int  `json:"limit"`
    Offset   int  `json:"offset"`
    HasMore  bool `json:"has_more"`
}

// NextOffset returns next offset for pagination
func (p PaginatedResult[T]) NextOffset() *int {
    if !p.HasMore {
        return nil
    }
    offset := p.Offset + p.Limit
    return &offset
}

// DatabaseStats contains statistics
type DatabaseStats struct {
    TotalObservations int                `json:"total_observations"`
    EarliestTimestamp *time.Time         `json:"earliest_timestamp,omitempty"`
    LatestTimestamp   *time.Time         `json:"latest_timestamp,omitempty"`
    Projects          []ProjectCount     `json:"projects"`
    Kinds             []KindCount        `json:"kinds"`
    DatabaseSizeBytes *int64             `json:"database_size_bytes,omitempty"`
}

// ProjectCount is a project with observation count
type ProjectCount struct {
    Project string `json:"project"`
    Count   int    `json:"count"`
}

// KindCount is a kind with observation count
type KindCount struct {
    Kind  string `json:"kind"`
    Count int    `json:"count"`
}
```

### Client

```go
// client.go
package losmemory

import (
    "bytes"
    "context"
    "encoding/json"
    "fmt"
    "os"
    "os/exec"
    "path/filepath"
    "strings"
    "time"
)

// Profile represents memory profiles
type Profile string

const (
    ProfileCodex  Profile = "codex"
    ProfileClaude Profile = "claude"
    ProfileShared Profile = "shared"
)

// Client is the main SDK client
type Client struct {
    profile  Profile
    dbPath   string
    cliPath  string
    timeout  time.Duration
}

// ClientOption configures the client
type ClientOption func(*Client)

// WithProfile sets the profile
func WithProfile(p Profile) ClientOption {
    return func(c *Client) {
        c.profile = p
    }
}

// WithDBPath sets the database path
func WithDBPath(path string) ClientOption {
    return func(c *Client) {
        c.dbPath = path
    }
}

// WithCLIPath sets the CLI path
func WithCLIPath(path string) ClientOption {
    return func(c *Client) {
        c.cliPath = path
    }
}

// WithTimeout sets the command timeout
func WithTimeout(d time.Duration) ClientOption {
    return func(c *Client) {
        c.timeout = d
    }
}

// NewClient creates a new client
func NewClient(opts ...ClientOption) *Client {
    c := &Client{
        profile: ProfileCodex,
        cliPath: "memory_tool",
        timeout: 30 * time.Second,
    }

    // Check environment
    if envProfile := os.Getenv("MEMORY_PROFILE"); envProfile != "" {
        c.profile = Profile(envProfile)
    }
    if envDB := os.Getenv("MEMORY_DB_PATH"); envDB != "" {
        c.dbPath = envDB
    }

    for _, opt := range opts {
        opt(c)
    }

    return c
}

// execute runs a CLI command and parses the result
func (c *Client) execute(ctx context.Context, args ...string) (*Response, error) {
    cmdArgs := []string{"--json"}

    if c.profile != "" {
        cmdArgs = append(cmdArgs, "--profile", string(c.profile))
    }
    if c.dbPath != "" {
        cmdArgs = append(cmdArgs, "--db", c.dbPath)
    }

    cmdArgs = append(cmdArgs, args...)

    ctx, cancel := context.WithTimeout(ctx, c.timeout)
    defer cancel()

    cmd := exec.CommandContext(ctx, c.cliPath, cmdArgs...)

    var stdout, stderr bytes.Buffer
    cmd.Stdout = &stdout
    cmd.Stderr = &stderr

    err := cmd.Run()

    // Parse JSON response regardless of exit code
    var resp Response
    if jsonErr := json.Unmarshal(stdout.Bytes(), &resp); jsonErr != nil {
        return nil, fmt.Errorf("failed to parse response: %w (stderr: %s)", jsonErr, stderr.String())
    }

    // Check for error response
    if !resp.OK {
        return &resp, c.parseError(&resp)
    }

    // Check exit error even if JSON parsed
    if err != nil {
        return &resp, c.parseError(&resp)
    }

    return &resp, nil
}

// Response is the base JSON response
type Response struct {
    SchemaVersion string                 `json:"schema_version"`
    Timestamp     time.Time              `json:"timestamp"`
    OK            bool                   `json:"ok"`
    Command       string                 `json:"command"`
    Profile       string                 `json:"profile"`
    DB            string                 `json:"db"`
    Data          json.RawMessage        `json:"data"`
    Error         *ErrorResponse         `json:"error,omitempty"`
    Meta          map[string]interface{} `json:"meta,omitempty"`
}

// ErrorResponse is the error details
type ErrorResponse struct {
    Code       string                 `json:"code"`
    Message    string                 `json:"message"`
    Category   string                 `json:"category"`
    Details    map[string]interface{} `json:"details,omitempty"`
    Suggestion string                 `json:"suggestion,omitempty"`
}

// parseError converts error response to MemoryError
func (c *Client) parseError(resp *Response) error {
    if resp.Error == nil {
        return &MemoryError{
            Code:    "UNKNOWN",
            Message: "unknown error occurred",
        }
    }

    return &MemoryError{
        Code:       ErrorCode(resp.Error.Code),
        Message:    resp.Error.Message,
        Category:   resp.Error.Category,
        Details:    resp.Error.Details,
        Suggestion: resp.Error.Suggestion,
    }
}

// AddObservation creates a new observation
func (c *Client) AddObservation(ctx context.Context, req AddObservationRequest) (*Observation, error) {
    args := []string{
        "add",
        "--title", req.Title,
        "--summary", req.Summary,
    }

    if req.Project != "" {
        args = append(args, "--project", req.Project)
    }
    if req.Kind != "" {
        args = append(args, "--kind", string(req.Kind))
    }
    if len(req.Tags) > 0 {
        args = append(args, "--tags", strings.Join(req.Tags, ","))
    }
    if req.Raw != "" {
        args = append(args, "--raw", req.Raw)
    }
    if req.AutoTags {
        args = append(args, "--auto-tags")
    }

    resp, err := c.execute(ctx, args...)
    if err != nil {
        return nil, err
    }

    var data struct {
        ID        int64  `json:"id"`
        SessionID *int64 `json:"session_id,omitempty"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    // Fetch the full observation
    return c.GetObservation(ctx, data.ID)
}

// AddObservationRequest is the input for AddObservation
type AddObservationRequest struct {
    Title     string
    Summary   string
    Project   string
    Kind      ObservationKind
    Tags      []string
    Raw       string
    AutoTags  bool
}

// GetObservation retrieves an observation by ID
func (c *Client) GetObservation(ctx context.Context, id int64) (*Observation, error) {
    resp, err := c.execute(ctx, "get", fmt.Sprintf("%d", id))
    if err != nil {
        return nil, err
    }

    var data struct {
        Results []Observation `json:"results"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    if len(data.Results) == 0 {
        return nil, &MemoryError{
            Code:    ErrNotFound,
            Message: fmt.Sprintf("observation %d not found", id),
        }
    }

    return &data.Results[0], nil
}

// Search searches observations
func (c *Client) Search(ctx context.Context, query string, opts *SearchOptions) (*PaginatedResult[SearchResult], error) {
    args := []string{"search", query}

    if opts != nil {
        if opts.Limit > 0 {
            args = append(args, "--limit", fmt.Sprintf("%d", opts.Limit))
        }
        if opts.Offset > 0 {
            args = append(args, "--offset", fmt.Sprintf("%d", opts.Offset))
        }
        if opts.Mode != "" {
            args = append(args, "--mode", opts.Mode)
        }
        if opts.Quote {
            args = append(args, "--fts-quote")
        }
        if len(opts.RequiredTags) > 0 {
            args = append(args, "--require-tags", strings.Join(opts.RequiredTags, ","))
        }
    }

    resp, err := c.execute(ctx, args...)
    if err != nil {
        return nil, err
    }

    var data struct {
        Query      string                  `json:"query"`
        Results    []SearchResult          `json:"results"`
        Pagination PaginatedResult[struct{}] `json:"pagination"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return &PaginatedResult[SearchResult]{
        Items:   data.Results,
        Total:   data.Pagination.Total,
        Limit:   data.Pagination.Limit,
        Offset:  data.Pagination.Offset,
        HasMore: data.Pagination.HasMore,
    }, nil
}

// SearchOptions configures search
type SearchOptions struct {
    Limit        int
    Offset       int
    Mode         string
    Quote        bool
    RequiredTags []string
}

// ListObservations lists recent observations
func (c *Client) ListObservations(ctx context.Context, opts *ListOptions) (*PaginatedResult[Observation], error) {
    args := []string{"list"}

    if opts != nil {
        if opts.Limit > 0 {
            args = append(args, "--limit", fmt.Sprintf("%d", opts.Limit))
        }
        if opts.Offset > 0 {
            args = append(args, "--offset", fmt.Sprintf("%d", opts.Offset))
        }
        if opts.Project != "" {
            args = append(args, "--project", opts.Project)
        }
        if opts.Kind != "" {
            args = append(args, "--kind", opts.Kind)
        }
    }

    resp, err := c.execute(ctx, args...)
    if err != nil {
        return nil, err
    }

    var data struct {
        Results    []Observation           `json:"results"`
        Pagination PaginatedResult[struct{}] `json:"pagination"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return &PaginatedResult[Observation]{
        Items:   data.Results,
        Total:   data.Pagination.Total,
        Limit:   data.Pagination.Limit,
        Offset:  data.Pagination.Offset,
        HasMore: data.Pagination.HasMore,
    }, nil
}

// ListOptions configures list
type ListOptions struct {
    Limit   int
    Offset  int
    Project string
    Kind    string
}

// UpdateObservation updates an observation
func (c *Client) UpdateObservation(ctx context.Context, id int64, req UpdateObservationRequest) (*Observation, error) {
    args := []string{"edit", "--id", fmt.Sprintf("%d", id)}

    if req.Title != nil {
        args = append(args, "--title", *req.Title)
    }
    if req.Summary != nil {
        args = append(args, "--summary", *req.Summary)
    }
    if req.Project != nil {
        args = append(args, "--project", *req.Project)
    }
    if req.Kind != nil {
        args = append(args, "--kind", string(*req.Kind))
    }
    if req.Tags != nil {
        args = append(args, "--tags", strings.Join(req.Tags, ","))
    }
    if req.Raw != nil {
        args = append(args, "--raw", *req.Raw)
    }
    if req.AutoTags {
        args = append(args, "--auto-tags")
    }

    _, err := c.execute(ctx, args...)
    if err != nil {
        return nil, err
    }

    return c.GetObservation(ctx, id)
}

// UpdateObservationRequest is the input for UpdateObservation
type UpdateObservationRequest struct {
    Title    *string
    Summary  *string
    Project  *string
    Kind     *ObservationKind
    Tags     []string
    Raw      *string
    AutoTags bool
}

// DeleteObservations deletes observations
func (c *Client) DeleteObservations(ctx context.Context, ids []int64) (int, error) {
    if len(ids) == 0 {
        return 0, nil
    }

    idStrs := make([]string, len(ids))
    for i, id := range ids {
        idStrs[i] = fmt.Sprintf("%d", id)
    }

    resp, err := c.execute(ctx, "delete", strings.Join(idStrs, ","))
    if err != nil {
        return 0, err
    }

    var data struct {
        Deleted int `json:"deleted"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return 0, err
    }

    return data.Deleted, nil
}

// StartSession starts a new session
func (c *Client) StartSession(ctx context.Context, req StartSessionRequest) (*Session, error) {
    args := []string{"session", "start"}

    if req.Project != "" {
        args = append(args, "--project", req.Project)
    }
    if req.WorkingDir != "" {
        args = append(args, "--working-dir", req.WorkingDir)
    }
    if req.AgentType != "" {
        args = append(args, "--agent-type", req.AgentType)
    }
    if req.Summary != "" {
        args = append(args, "--summary", req.Summary)
    }

    resp, err := c.execute(ctx, args...)
    if err != nil {
        return nil, err
    }

    var data struct {
        SessionID int64 `json:"session_id"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return c.GetSession(ctx, data.SessionID)
}

// StartSessionRequest is the input for StartSession
type StartSessionRequest struct {
    Project    string
    WorkingDir string
    AgentType  string
    Summary    string
}

// GetSession retrieves a session
func (c *Client) GetSession(ctx context.Context, id int64) (*Session, error) {
    resp, err := c.execute(ctx, "session", "show", fmt.Sprintf("%d", id))
    if err != nil {
        return nil, err
    }

    var data struct {
        Session Session `json:"session"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return &data.Session, nil
}

// GetActiveSession gets the active session
func (c *Client) GetActiveSession(ctx context.Context) (*Session, error) {
    // Resume without ID returns active session info
    resp, err := c.execute(ctx, "session", "resume")
    if err != nil {
        // Check if it's "no active session" error
        if memErr, ok := err.(*MemoryError); ok && memErr.Code == "VAL_NO_ACTIVE_SESSION" {
            return nil, nil
        }
        return nil, err
    }

    var data struct {
        SessionID int64 `json:"session_id"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return c.GetSession(ctx, data.SessionID)
}

// EndSession ends the active session
func (c *Client) EndSession(ctx context.Context, summary string) (*Session, error) {
    args := []string{"session", "stop"}
    if summary != "" {
        args = append(args, "--summary", summary)
    }

    resp, err := c.execute(ctx, args...)
    if err != nil {
        return nil, err
    }

    var data struct {
        SessionID int64  `json:"session_id"`
        Summary   string `json:"summary"`
    }
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return c.GetSession(ctx, data.SessionID)
}

// GetStats retrieves database statistics
func (c *Client) GetStats(ctx context.Context) (*DatabaseStats, error) {
    resp, err := c.execute(ctx, "manage", "stats")
    if err != nil {
        return nil, err
    }

    var data DatabaseStats
    if err := json.Unmarshal(resp.Data, &data); err != nil {
        return nil, err
    }

    return &data, nil
}
```

### Go Usage Examples

```go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/los/memory/sdk/go"
)

func main() {
    // Create client
    client := losmemory.NewClient(
        losmemory.WithProfile(losmemory.ProfileClaude),
        losmemory.WithTimeout(30*time.Second),
    )

    ctx := context.Background()

    // Add observation
    obs, err := client.AddObservation(ctx, losmemory.AddObservationRequest{
        Title:   "API Design Decision",
        Summary: "Decided to use REST over GraphQL",
        Kind:    losmemory.KindDecision,
        Tags:    []string{"api", "rest"},
    })
    if err != nil {
        log.Fatal(err)
    }
    fmt.Printf("Created observation %d\n", obs.ID)

    // Search
    results, err := client.Search(ctx, "API design", &losmemory.SearchOptions{
        Limit: 10,
    })
    if err != nil {
        log.Fatal(err)
    }

    for _, r := range results.Items {
        fmt.Printf("%s (score: %.2f)\n", r.Observation.Title, *r.Score)
    }

    // Pagination
    if results.HasMore {
        nextResults, err := client.Search(ctx, "API design", &losmemory.SearchOptions{
            Limit:  results.Limit,
            Offset: *results.NextOffset(),
        })
        // ...
    }

    // Sessions
    session, err := client.StartSession(ctx, losmemory.StartSessionRequest{
        Project: "myapp",
    })
    if err != nil {
        log.Fatal(err)
    }

    // Add observation in session
    obs, _ = client.AddObservation(ctx, losmemory.AddObservationRequest{
        Title:   "Session work",
        Summary: "Work done in session",
    })

    // End session
    _, err = client.EndSession(ctx, "Completed feature X")
}
```

---

## Rust SDK Design

### Package Structure

```
losmemory/
├── Cargo.toml
├── src/
│   ├── lib.rs           # Public exports
│   ├── client.rs        # Main client
│   ├── models.rs        # Data models
│   ├── error.rs         # Error types
│   ├── options.rs       # Builder patterns
│   └── cmd.rs           # Command execution
└── examples/
    └── basic.rs
```

### Cargo.toml

```toml
[package]
name = "losmemory"
version = "1.0.0"
edition = "2021"

[dependencies]
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tokio = { version = "1.0", features = ["process", "time"] }
chrono = { version = "0.4", features = ["serde"] }
thiserror = "1.0"
```

### Error Types

```rust
// error.rs
use thiserror::Error;

/// Error codes for memory tool errors
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    Validation,
    NotFound,
    Database,
    SchemaVersion,
    Permission,
    Conflict,
    Unknown,
}

impl std::fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ErrorCode::Validation => write!(f, "VAL_INVALID_INPUT"),
            ErrorCode::NotFound => write!(f, "NF_OBSERVATION"),
            ErrorCode::Database => write!(f, "DB_ERROR"),
            ErrorCode::SchemaVersion => write!(f, "DB_SCHEMA_VERSION"),
            ErrorCode::Permission => write!(f, "SYS_PERMISSION"),
            ErrorCode::Conflict => write!(f, "CONF_DUPLICATE"),
            ErrorCode::Unknown => write!(f, "UNKNOWN"),
        }
    }
}

/// Main error type for los-memory
#[derive(Error, Debug)]
pub enum MemoryError {
    #[error("[{code}] {message}")]
    ApiError {
        code: ErrorCode,
        message: String,
        category: String,
        details: Option<serde_json::Value>,
        suggestion: Option<String>,
    },

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("Command timeout")]
    Timeout,

    #[error("CLI not found: {0}")]
    CliNotFound(String),
}

impl MemoryError {
    /// Check if error is not found
    pub fn is_not_found(&self) -> bool {
        matches!(self, MemoryError::ApiError { code: ErrorCode::NotFound, .. })
    }

    /// Get suggestion if available
    pub fn suggestion(&self) -> Option<&str> {
        match self {
            MemoryError::ApiError { suggestion, .. } => suggestion.as_deref(),
            _ => None,
        }
    }
}

/// Result type alias
pub type Result<T> = std::result::Result<T, MemoryError>;
```

### Models

```rust
// models.rs
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Observation kinds
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObservationKind {
    Note,
    Decision,
    Fix,
    Incident,
}

impl Default for ObservationKind {
    fn default() -> Self {
        ObservationKind::Note
    }
}

/// Observation model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Observation {
    pub id: i64,
    pub timestamp: DateTime<Utc>,
    pub project: String,
    #[serde(rename = "kind")]
    pub kind: ObservationKind,
    pub title: String,
    pub summary: String,
    pub tags: Vec<String>,
    pub raw: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<i64>,
}

/// Session model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: i64,
    pub start_time: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub end_time: Option<DateTime<Utc>>,
    pub project: String,
    pub working_dir: String,
    pub agent_type: String,
    pub summary: String,
    pub status: String,
}

impl Session {
    /// Check if session is active
    pub fn is_active(&self) -> bool {
        self.status == "active"
    }

    /// Get session duration in seconds
    pub fn duration(&self) -> Option<i64> {
        self.end_time.map(|end| (end - self.start_time).num_seconds())
    }
}

/// Checkpoint model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Checkpoint {
    pub id: i64,
    pub timestamp: DateTime<Utc>,
    pub name: String,
    pub description: String,
    pub tag: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<i64>,
    pub observation_count: i32,
    pub project: String,
}

/// Search result with score
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    #[serde(flatten)]
    pub observation: Observation,
    pub score: Option<f64>,
}

/// Paginated results
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaginatedResult<T> {
    pub items: Vec<T>,
    pub total: usize,
    pub limit: usize,
    pub offset: usize,
    #[serde(rename = "has_more")]
    pub has_more: bool,
}

impl<T> PaginatedResult<T> {
    /// Get next offset for pagination
    pub fn next_offset(&self) -> Option<usize> {
        if self.has_more {
            Some(self.offset + self.limit)
        } else {
            None
        }
    }

    /// Iterate over items
    pub fn iter(&self) -> impl Iterator<Item = &T> {
        self.items.iter()
    }
}

impl<T> IntoIterator for PaginatedResult<T> {
    type Item = T;
    type IntoIter = std::vec::IntoIter<T>;

    fn into_iter(self) -> Self::IntoIter {
        self.items.into_iter()
    }
}

/// Database statistics
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatabaseStats {
    pub total_observations: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub earliest_timestamp: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub latest_timestamp: Option<DateTime<Utc>>,
    pub projects: Vec<ProjectCount>,
    pub kinds: Vec<KindCount>,
}

/// Project with count
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectCount {
    pub project: String,
    pub count: i64,
}

/// Kind with count
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KindCount {
    pub kind: String,
    pub count: i64,
}

/// Profile options
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Profile {
    Codex,
    Claude,
    Shared,
}

impl std::fmt::Display for Profile {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Profile::Codex => write!(f, "codex"),
            Profile::Claude => write!(f, "claude"),
            Profile::Shared => write!(f, "shared"),
        }
    }
}

impl Default for Profile {
    fn default() -> Self {
        Profile::Codex
    }
}
```

### Client

```rust
// client.rs
use crate::{
    error::{ErrorCode, MemoryError, Result},
    models::*,
};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;
use tokio::time::timeout;

/// Client configuration
#[derive(Debug, Clone)]
pub struct ClientConfig {
    pub profile: Profile,
    pub db_path: Option<String>,
    pub cli_path: String,
    pub timeout: Duration,
}

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            profile: Profile::default(),
            db_path: None,
            cli_path: "memory_tool".to_string(),
            timeout: Duration::from_secs(30),
        }
    }
}

impl ClientConfig {
    /// Create config from environment
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(profile) = std::env::var("MEMORY_PROFILE") {
            config.profile = match profile.as_str() {
                "claude" => Profile::Claude,
                "shared" => Profile::Shared,
                _ => Profile::Codex,
            };
        }

        if let Ok(db_path) = std::env::var("MEMORY_DB_PATH") {
            config.db_path = Some(db_path);
        }

        config
    }
}

/// Main client for los-memory
#[derive(Debug, Clone)]
pub struct Client {
    config: ClientConfig,
}

impl Client {
    /// Create new client with default config
    pub fn new() -> Self {
        Self {
            config: ClientConfig::from_env(),
        }
    }

    /// Create client with custom config
    pub fn with_config(config: ClientConfig) -> Self {
        Self { config }
    }

    /// Execute CLI command
    async fn execute<T: DeserializeOwned>(
        &self,
        args: &[&str],
    ) -> Result<T> {
        let mut cmd_args = vec!["--json"];

        cmd_args.push("--profile");
        cmd_args.push(&self.config.profile.to_string());

        if let Some(ref db_path) = self.config.db_path {
            cmd_args.push("--db");
            cmd_args.push(db_path);
        }

        cmd_args.extend(args);

        let output = timeout(
            self.config.timeout,
            Command::new(&self.config.cli_path)
                .args(&cmd_args)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .output(),
        )
        .await
        .map_err(|_| MemoryError::Timeout)??;

        let stdout = String::from_utf8_lossy(&output.stdout);

        // Parse response
        let response: Value = serde_json::from_str(&stdout)
            .map_err(|e| MemoryError::Json(e))?;

        // Check for error
        if let Some(false) = response.get("ok").and_then(|v| v.as_bool()) {
            return Err(self.parse_error(&response));
        }

        // Check exit code
        if !output.status.success() {
            return Err(self.parse_error(&response));
        }

        // Extract data field
        let data = response.get("data")
            .ok_or_else(|| MemoryError::ApiError {
                code: ErrorCode::Unknown,
                message: "Missing data field in response".to_string(),
                category: "response".to_string(),
                details: None,
                suggestion: None,
            })?;

        serde_json::from_value(data.clone())
            .map_err(|e| MemoryError::Json(e))
    }

    /// Parse error from response
    fn parse_error(&self, response: &Value) -> MemoryError {
        if let Some(error) = response.get("error") {
            let code = error.get("code")
                .and_then(|v| v.as_str())
                .map(|s| match s {
                    "VAL_INVALID_INPUT" => ErrorCode::Validation,
                    "NF_OBSERVATION" => ErrorCode::NotFound,
                    "DB_ERROR" => ErrorCode::Database,
                    "DB_SCHEMA_VERSION" => ErrorCode::SchemaVersion,
                    "SYS_PERMISSION" => ErrorCode::Permission,
                    "CONF_DUPLICATE" => ErrorCode::Conflict,
                    _ => ErrorCode::Unknown,
                })
                .unwrap_or(ErrorCode::Unknown);

            MemoryError::ApiError {
                code,
                message: error.get("message").and_then(|v| v.as_str()).unwrap_or("Unknown error").to_string(),
                category: error.get("category").and_then(|v| v.as_str()).unwrap_or("unknown").to_string(),
                details: error.get("details").cloned(),
                suggestion: error.get("suggestion").and_then(|v| v.as_str()).map(|s| s.to_string()),
            }
        } else {
            MemoryError::ApiError {
                code: ErrorCode::Unknown,
                message: "Unknown error occurred".to_string(),
                category: "unknown".to_string(),
                details: None,
                suggestion: None,
            }
        }
    }

    // Observation Operations

    /// Add a new observation
    pub async fn add_observation(
        &self,
        title: &str,
        summary: &str,
    ) -> Result<Observation> {
        self.add_observation_with_options(title, summary, AddObservationOptions::default()).await
    }

    /// Add observation with options
    pub async fn add_observation_with_options(
        &self,
        title: &str,
        summary: &str,
        options: AddObservationOptions,
    ) -> Result<Observation> {
        let mut args = vec!["add", "--title", title, "--summary", summary];

        if let Some(project) = options.project {
            args.extend(["--project", project]);
        }
        if let Some(kind) = options.kind {
            args.extend(["--kind", &kind.to_string()]);
        }
        if !options.tags.is_empty() {
            args.extend(["--tags", &options.tags.join(",")]);
        }
        if let Some(raw) = options.raw {
            args.extend(["--raw", raw]);
        }
        if options.auto_tags {
            args.push("--auto-tags");
        }

        #[derive(Deserialize)]
        struct AddResponse {
            id: i64,
        }

        let resp: AddResponse = self.execute(&args).await?;
        self.get_observation(resp.id).await
    }

    /// Get observation by ID
    pub async fn get_observation(&self, id: i64) -> Result<Observation> {
        #[derive(Deserialize)]
        struct GetResponse {
            results: Vec<Observation>,
        }

        let resp: GetResponse = self.execute(&["get", &id.to_string()]).await?;

        resp.results.into_iter()
            .next()
            .ok_or_else(|| MemoryError::ApiError {
                code: ErrorCode::NotFound,
                message: format!("Observation {} not found", id),
                category: "not_found".to_string(),
                details: None,
                suggestion: None,
            })
    }

    /// Search observations
    pub async fn search(
        &self,
        query: &str,
        options: SearchOptions,
    ) -> Result<PaginatedResult<SearchResult>> {
        let mut args = vec!["search", query];

        if let Some(limit) = options.limit {
            args.extend(["--limit", &limit.to_string()]);
        }
        if let Some(offset) = options.offset {
            args.extend(["--offset", &offset.to_string()]);
        }
        if let Some(mode) = options.mode {
            args.extend(["--mode", mode]);
        }
        if options.quote {
            args.push("--fts-quote");
        }
        if !options.required_tags.is_empty() {
            args.extend(["--require-tags", &options.required_tags.join(",")]);
        }

        #[derive(Deserialize)]
        struct SearchResponse {
            results: Vec<SearchResult>,
            pagination: PaginationInfo,
        }

        #[derive(Deserialize)]
        struct PaginationInfo {
            total: usize,
            limit: usize,
            offset: usize,
            has_more: bool,
        }

        let resp: SearchResponse = self.execute(&args).await?;

        Ok(PaginatedResult {
            items: resp.results,
            total: resp.pagination.total,
            limit: resp.pagination.limit,
            offset: resp.pagination.offset,
            has_more: resp.pagination.has_more,
        })
    }

    /// List recent observations
    pub async fn list_observations(
        &self,
        options: ListOptions,
    ) -> Result<PaginatedResult<Observation>> {
        let mut args = vec!["list"];

        if let Some(limit) = options.limit {
            args.extend(["--limit", &limit.to_string()]);
        }
        if let Some(offset) = options.offset {
            args.extend(["--offset", &offset.to_string()]);
        }
        if let Some(project) = options.project {
            args.extend(["--project", project]);
        }
        if let Some(kind) = options.kind {
            args.extend(["--kind", kind]);
        }

        #[derive(Deserialize)]
        struct ListResponse {
            results: Vec<Observation>,
            pagination: PaginationInfo,
        }

        #[derive(Deserialize)]
        struct PaginationInfo {
            total: usize,
            limit: usize,
            offset: usize,
            has_more: bool,
        }

        let resp: ListResponse = self.execute(&args).await?;

        Ok(PaginatedResult {
            items: resp.results,
            total: resp.pagination.total,
            limit: resp.pagination.limit,
            offset: resp.pagination.offset,
            has_more: resp.pagination.has_more,
        })
    }

    /// Update observation
    pub async fn update_observation(
        &self,
        id: i64,
        options: UpdateObservationOptions,
    ) -> Result<Observation> {
        let mut args = vec!["edit", "--id", &id.to_string()];

        if let Some(title) = options.title {
            args.extend(["--title", title]);
        }
        if let Some(summary) = options.summary {
            args.extend(["--summary", summary]);
        }
        if let Some(project) = options.project {
            args.extend(["--project", project]);
        }
        if let Some(kind) = options.kind {
            args.extend(["--kind", &kind.to_string()]);
        }
        if !options.tags.is_empty() {
            args.extend(["--tags", &options.tags.join(",")]);
        }
        if let Some(raw) = options.raw {
            args.extend(["--raw", raw]);
        }
        if options.auto_tags {
            args.push("--auto-tags");
        }

        self.execute::<Value>(&args).await?;
        self.get_observation(id).await
    }

    /// Delete observations
    pub async fn delete_observations(&self, ids: &[i64]) -> Result<usize> {
        if ids.is_empty() {
            return Ok(0);
        }

        let id_str: Vec<String> = ids.iter().map(|id| id.to_string()).collect();
        let args: Vec<&str> = std::iter::once("delete")
            .chain(id_str.iter().map(|s| s.as_str()))
            .collect();

        #[derive(Deserialize)]
        struct DeleteResponse {
            deleted: usize,
        }

        let resp: DeleteResponse = self.execute(&args).await?;
        Ok(resp.deleted)
    }

    // Session Operations

    /// Start a new session
    pub async fn start_session(
        &self,
        options: StartSessionOptions,
    ) -> Result<Session> {
        let mut args = vec!["session", "start"];

        if let Some(project) = options.project {
            args.extend(["--project", project]);
        }
        if let Some(working_dir) = options.working_dir {
            args.extend(["--working-dir", working_dir]);
        }
        if let Some(agent_type) = options.agent_type {
            args.extend(["--agent-type", agent_type]);
        }
        if let Some(summary) = options.summary {
            args.extend(["--summary", summary]);
        }

        #[derive(Deserialize)]
        struct StartResponse {
            session_id: i64,
        }

        let resp: StartResponse = self.execute(&args).await?;
        self.get_session(resp.session_id).await
    }

    /// Get session by ID
    pub async fn get_session(&self, id: i64) -> Result<Session> {
        #[derive(Deserialize)]
        struct ShowResponse {
            session: Session,
        }

        let resp: ShowResponse = self.execute(&["session", "show", &id.to_string()]).await?;
        Ok(resp.session)
    }

    /// Get active session
    pub async fn get_active_session(&self) -> Result<Option<Session>> {
        match self.execute::<Value>(&["session", "resume"]).await {
            Ok(val) => {
                if let Some(id) = val.get("session_id").and_then(|v| v.as_i64()) {
                    self.get_session(id).await.map(Some)
                } else {
                    Ok(None)
                }
            }
            Err(MemoryError::ApiError { code: ErrorCode::Validation, .. }) => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// End active session
    pub async fn end_session(&self, summary: Option<&str>) -> Result<Session> {
        let mut args = vec!["session", "stop"];

        if let Some(summary) = summary {
            args.extend(["--summary", summary]);
        }

        #[derive(Deserialize)]
        struct StopResponse {
            session_id: i64,
        }

        let resp: StopResponse = self.execute(&args).await?;
        self.get_session(resp.session_id).await
    }

    // Statistics

    /// Get database statistics
    pub async fn get_stats(&self) -> Result<DatabaseStats> {
        self.execute(&["manage", "stats"]).await
    }
}

/// Options for adding observations
#[derive(Debug, Default)]
pub struct AddObservationOptions<'a> {
    pub project: Option<&'a str>,
    pub kind: Option<ObservationKind>,
    pub tags: Vec<&'a str>,
    pub raw: Option<&'a str>,
    pub auto_tags: bool,
}

/// Options for searching
#[derive(Debug, Default)]
pub struct SearchOptions {
    pub limit: Option<usize>,
    pub offset: Option<usize>,
    pub mode: Option<&'static str>,
    pub quote: bool,
    pub required_tags: Vec<String>,
}

/// Options for listing
#[derive(Debug, Default)]
pub struct ListOptions<'a> {
    pub limit: Option<usize>,
    pub offset: Option<usize>,
    pub project: Option<&'a str>,
    pub kind: Option<&'a str>,
}

/// Options for updating
#[derive(Debug, Default)]
pub struct UpdateObservationOptions<'a> {
    pub title: Option<&'a str>,
    pub summary: Option<&'a str>,
    pub project: Option<&'a str>,
    pub kind: Option<ObservationKind>,
    pub tags: Vec<&'a str>,
    pub raw: Option<&'a str>,
    pub auto_tags: bool,
}

/// Options for starting session
#[derive(Debug, Default)]
pub struct StartSessionOptions<'a> {
    pub project: Option<&'a str>,
    pub working_dir: Option<&'a str>,
    pub agent_type: Option<&'a str>,
    pub summary: Option<&'a str>,
}
```

### Rust Usage Examples

```rust
use losmemory::{Client, Profile, SearchOptions};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Create client
    let client = Client::with_config(
        ClientConfig {
            profile: Profile::Claude,
            ..Default::default()
        }
    );

    // Add observation
    let obs = client.add_observation_with_options(
        "API Design Decision",
        "Decided to use REST over GraphQL",
        AddObservationOptions {
            kind: Some(ObservationKind::Decision),
            tags: vec!["api", "rest"],
            ..Default::default()
        }
    ).await?;

    println!("Created observation {}", obs.id);

    // Search
    let results = client.search(
        "API design",
        SearchOptions {
            limit: Some(10),
            ..Default::default()
        }
    ).await?;

    for result in &results {
        println!("{} (score: {:?})", result.observation.title, result.score);
    }

    // Pagination
    if results.has_more {
        let next_page = client.search(
            "API design",
            SearchOptions {
                limit: Some(results.limit),
                offset: results.next_offset(),
                ..Default::default()
            }
        ).await?;
    }

    // Sessions
    let session = client.start_session(
        StartSessionOptions {
            project: Some("myapp"),
            ..Default::default()
        }
    ).await?;

    // End session
    let ended = client.end_session(Some("Completed feature X")).await?;
    println!("Session lasted {:?} seconds", ended.duration());

    Ok(())
}
```

---

## Node.js SDK Design

### Package Structure

```
losmemory/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts       # Public exports
│   ├── client.ts      # Main client
│   ├── models.ts      # TypeScript interfaces
│   ├── errors.ts      # Error classes
│   └── types.ts       # Type definitions
└── dist/              # Compiled output
```

### package.json

```json
{
  "name": "@los/memory",
  "version": "1.0.0",
  "description": "Node.js SDK for los-memory",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "scripts": {
    "build": "tsc",
    "test": "jest"
  },
  "dependencies": {},
  "devDependencies": {
    "@types/node": "^20.0.0",
    "typescript": "^5.0.0"
  }
}
```

### Error Types

```typescript
// errors.ts

export enum ErrorCode {
  VALIDATION = 'VAL_INVALID_INPUT',
  NOT_FOUND = 'NF_OBSERVATION',
  DATABASE = 'DB_ERROR',
  SCHEMA_VERSION = 'DB_SCHEMA_VERSION',
  PERMISSION = 'SYS_PERMISSION',
  CONFLICT = 'CONF_DUPLICATE',
  UNKNOWN = 'UNKNOWN',
}

export interface ErrorDetails {
  [key: string]: unknown;
}

export class MemoryError extends Error {
  public readonly code: ErrorCode;
  public readonly category: string;
  public readonly details?: ErrorDetails;
  public readonly suggestion?: string;

  constructor(
    code: ErrorCode,
    message: string,
    category: string,
    details?: ErrorDetails,
    suggestion?: string
  ) {
    super(`[${code}] ${message}`);
    this.name = 'MemoryError';
    this.code = code;
    this.category = category;
    this.details = details;
    this.suggestion = suggestion;
  }

  static isNotFound(error: unknown): error is MemoryError {
    return error instanceof MemoryError && error.code === ErrorCode.NOT_FOUND;
  }
}

export class ValidationError extends MemoryError {
  public readonly field?: string;

  constructor(message: string, field?: string) {
    super(ErrorCode.VALIDATION, message, 'validation', { field });
    this.name = 'ValidationError';
    this.field = field;
  }
}

export class NotFoundError extends MemoryError {
  public readonly resourceId: string | number;

  constructor(resource: string, id: string | number) {
    super(
      ErrorCode.NOT_FOUND,
      `${resource} ${id} not found`,
      'not_found',
      { resource, id }
    );
    this.name = 'NotFoundError';
    this.resourceId = id;
  }
}
```

### Models

```typescript
// models.ts

export type ObservationKind = 'note' | 'decision' | 'fix' | 'incident';

export interface Observation {
  id: number;
  timestamp: string;
  project: string;
  kind: ObservationKind;
  title: string;
  summary: string;
  tags: string[];
  raw: string;
  sessionId?: number;
}

export interface Session {
  id: number;
  startTime: string;
  endTime?: string;
  project: string;
  workingDir: string;
  agentType: string;
  summary: string;
  status: 'active' | 'completed';
}

export interface SessionWithDuration extends Session {
  durationSeconds?: number;
}

export interface Checkpoint {
  id: number;
  timestamp: string;
  name: string;
  description: string;
  tag: string;
  sessionId?: number;
  observationCount: number;
  project: string;
}

export interface SearchResult {
  observation: Observation;
  score?: number;
}

export interface PaginatedResult<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export interface PaginationInfo {
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  nextOffset?: number;
}

export interface ProjectCount {
  project: string;
  count: number;
}

export interface KindCount {
  kind: string;
  count: number;
}

export interface DatabaseStats {
  totalObservations: number;
  earliestTimestamp?: string;
  latestTimestamp?: string;
  projects: ProjectCount[];
  kinds: KindCount[];
  databaseSizeBytes?: number;
}

export type Profile = 'codex' | 'claude' | 'shared';

export type SearchMode = 'auto' | 'fts' | 'like';
```

### Client

```typescript
// client.ts
import { spawn } from 'child_process';
import { promisify } from 'util';
import {
  Observation,
  Session,
  Checkpoint,
  SearchResult,
  PaginatedResult,
  DatabaseStats,
  Profile,
  SearchMode,
  ObservationKind,
} from './models';
import { MemoryError, ErrorCode, NotFoundError } from './errors';

const execAsync = promisify(exec);

export interface ClientConfig {
  profile?: Profile;
  dbPath?: string;
  cliPath?: string;
  timeoutMs?: number;
}

export interface AddObservationOptions {
  project?: string;
  kind?: ObservationKind;
  tags?: string[];
  raw?: string;
  autoTags?: boolean;
}

export interface SearchOptions {
  limit?: number;
  offset?: number;
  mode?: SearchMode;
  quote?: boolean;
  requiredTags?: string[];
}

export interface ListOptions {
  limit?: number;
  offset?: number;
  project?: string;
  kind?: string;
}

export interface UpdateObservationOptions {
  title?: string;
  summary?: string;
  project?: string;
  kind?: ObservationKind;
  tags?: string[];
  raw?: string;
  autoTags?: boolean;
}

export interface StartSessionOptions {
  project?: string;
  workingDir?: string;
  agentType?: string;
  summary?: string;
}

interface ApiResponse<T = unknown> {
  schemaVersion: string;
  timestamp: string;
  ok: boolean;
  command: string;
  profile: string;
  db: string;
  data: T;
  error?: {
    code: string;
    message: string;
    category: string;
    details?: Record<string, unknown>;
    suggestion?: string;
  };
  meta?: {
    durationMs?: number;
    rowCount?: number;
  };
}

export class MemoryClient {
  private readonly profile: Profile;
  private readonly dbPath?: string;
  private readonly cliPath: string;
  private readonly timeoutMs: number;

  constructor(config: ClientConfig = {}) {
    this.profile = config.profile ?? this.getEnvProfile() ?? 'codex';
    this.dbPath = config.dbPath ?? process.env.MEMORY_DB_PATH;
    this.cliPath = config.cliPath ?? 'memory_tool';
    this.timeoutMs = config.timeoutMs ?? 30000;
  }

  private getEnvProfile(): Profile | undefined {
    const env = process.env.MEMORY_PROFILE?.toLowerCase();
    if (env === 'claude' || env === 'shared' || env === 'codex') {
      return env;
    }
    return undefined;
  }

  private async execute<T>(args: string[]): Promise<T> {
    const cmdArgs = ['--json'];

    cmdArgs.push('--profile', this.profile);

    if (this.dbPath) {
      cmdArgs.push('--db', this.dbPath);
    }

    cmdArgs.push(...args);

    return new Promise((resolve, reject) => {
      const child = spawn(this.cliPath, cmdArgs, {
        stdio: ['ignore', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';

      child.stdout!.on('data', (data) => {
        stdout += data.toString();
      });

      child.stderr!.on('data', (data) => {
        stderr += data.toString();
      });

      const timeout = setTimeout(() => {
        child.kill();
        reject(new MemoryError(
          ErrorCode.UNKNOWN,
          'Command timeout',
          'timeout',
          { timeoutMs: this.timeoutMs }
        ));
      }, this.timeoutMs);

      child.on('close', (code) => {
        clearTimeout(timeout);

        let response: ApiResponse;
        try {
          response = JSON.parse(stdout);
        } catch (e) {
          reject(new MemoryError(
            ErrorCode.UNKNOWN,
            `Failed to parse response: ${e}`,
            'parse_error',
            { stdout, stderr }
          ));
          return;
        }

        if (!response.ok || code !== 0) {
          reject(this.parseError(response));
          return;
        }

        resolve(response.data as T);
      });

      child.on('error', (err) => {
        clearTimeout(timeout);
        reject(new MemoryError(
          ErrorCode.UNKNOWN,
          `Failed to execute CLI: ${err.message}`,
          'execution_error'
        ));
      });
    });
  }

  private parseError(response: ApiResponse): MemoryError {
    const error = response.error;
    if (!error) {
      return new MemoryError(
        ErrorCode.UNKNOWN,
        'Unknown error occurred',
        'unknown'
      );
    }

    const codeMap: Record<string, ErrorCode> = {
      'VAL_INVALID_INPUT': ErrorCode.VALIDATION,
      'NF_OBSERVATION': ErrorCode.NOT_FOUND,
      'DB_ERROR': ErrorCode.DATABASE,
      'DB_SCHEMA_VERSION': ErrorCode.SCHEMA_VERSION,
      'SYS_PERMISSION': ErrorCode.PERMISSION,
      'CONF_DUPLICATE': ErrorCode.CONFLICT,
    };

    return new MemoryError(
      codeMap[error.code] ?? ErrorCode.UNKNOWN,
      error.message,
      error.category,
      error.details,
      error.suggestion
    );
  }

  // Observation Operations

  async addObservation(
    title: string,
    summary: string,
    options: AddObservationOptions = {}
  ): Promise<Observation> {
    const args = ['add', '--title', title, '--summary', summary];

    if (options.project) {
      args.push('--project', options.project);
    }
    if (options.kind) {
      args.push('--kind', options.kind);
    }
    if (options.tags?.length) {
      args.push('--tags', options.tags.join(','));
    }
    if (options.raw) {
      args.push('--raw', options.raw);
    }
    if (options.autoTags) {
      args.push('--auto-tags');
    }

    const result = await this.execute<{ id: number; sessionId?: number }>(args);
    return this.getObservation(result.id);
  }

  async getObservation(id: number): Promise<Observation> {
    const result = await this.execute<{ results: Observation[] }>([
      'get', id.toString()
    ]);

    const obs = result.results[0];
    if (!obs) {
      throw new NotFoundError('Observation', id);
    }

    return obs;
  }

  async getObservations(ids: number[]): Promise<Observation[]> {
    if (ids.length === 0) return [];

    const result = await this.execute<{ results: Observation[] }>([
      'get', ids.join(',')
    ]);

    return result.results;
  }

  async search(
    query: string,
    options: SearchOptions = {}
  ): Promise<PaginatedResult<SearchResult>> {
    const args = ['search', query];

    if (options.limit) {
      args.push('--limit', options.limit.toString());
    }
    if (options.offset) {
      args.push('--offset', options.offset.toString());
    }
    if (options.mode) {
      args.push('--mode', options.mode);
    }
    if (options.quote) {
      args.push('--fts-quote');
    }
    if (options.requiredTags?.length) {
      args.push('--require-tags', options.requiredTags.join(','));
    }

    const result = await this.execute<{
      query: string;
      results: SearchResult[];
      pagination: {
        total: number;
        limit: number;
        offset: number;
        hasMore: boolean;
      };
    }>(args);

    return {
      items: result.results,
      total: result.pagination.total,
      limit: result.pagination.limit,
      offset: result.pagination.offset,
      hasMore: result.pagination.hasMore,
    };
  }

  async listObservations(
    options: ListOptions = {}
  ): Promise<PaginatedResult<Observation>> {
    const args = ['list'];

    if (options.limit) {
      args.push('--limit', options.limit.toString());
    }
    if (options.offset) {
      args.push('--offset', options.offset.toString());
    }
    if (options.project) {
      args.push('--project', options.project);
    }
    if (options.kind) {
      args.push('--kind', options.kind);
    }

    const result = await this.execute<{
      results: Observation[];
      pagination: {
        total: number;
        limit: number;
        offset: number;
        hasMore: boolean;
      };
    }>(args);

    return {
      items: result.results,
      total: result.pagination.total,
      limit: result.pagination.limit,
      offset: result.pagination.offset,
      hasMore: result.pagination.hasMore,
    };
  }

  async updateObservation(
    id: number,
    options: UpdateObservationOptions
  ): Promise<Observation> {
    const args = ['edit', '--id', id.toString()];

    if (options.title) {
      args.push('--title', options.title);
    }
    if (options.summary) {
      args.push('--summary', options.summary);
    }
    if (options.project) {
      args.push('--project', options.project);
    }
    if (options.kind) {
      args.push('--kind', options.kind);
    }
    if (options.tags?.length) {
      args.push('--tags', options.tags.join(','));
    }
    if (options.raw) {
      args.push('--raw', options.raw);
    }
    if (options.autoTags) {
      args.push('--auto-tags');
    }

    await this.execute<unknown>(args);
    return this.getObservation(id);
  }

  async deleteObservations(ids: number[]): Promise<number> {
    if (ids.length === 0) return 0;

    const result = await this.execute<{
      deleted: number;
    }>(['delete', ids.join(',')]);

    return result.deleted;
  }

  // Session Operations

  async startSession(options: StartSessionOptions = {}): Promise<Session> {
    const args = ['session', 'start'];

    if (options.project) {
      args.push('--project', options.project);
    }
    if (options.workingDir) {
      args.push('--working-dir', options.workingDir);
    }
    if (options.agentType) {
      args.push('--agent-type', options.agentType);
    }
    if (options.summary) {
      args.push('--summary', options.summary);
    }

    const result = await this.execute<{ sessionId: number }>(args);
    return this.getSession(result.sessionId);
  }

  async getSession(id: number): Promise<Session> {
    const result = await this.execute<{ session: Session }>([
      'session', 'show', id.toString()
    ]);
    return result.session;
  }

  async getActiveSession(): Promise<Session | null> {
    try {
      const result = await this.execute<{ sessionId: number }>([
        'session', 'resume'
      ]);
      return this.getSession(result.sessionId);
    } catch (error) {
      if (MemoryError.isNotFound(error)) {
        return null;
      }
      throw error;
    }
  }

  async endSession(summary?: string): Promise<Session> {
    const args = ['session', 'stop'];
    if (summary) {
      args.push('--summary', summary);
    }

    const result = await this.execute<{ sessionId: number }>(args);
    return this.getSession(result.sessionId);
  }

  // Statistics

  async getStats(): Promise<DatabaseStats> {
    return this.execute<DatabaseStats>(['manage', 'stats']);
  }

  // Utility Methods

  async *paginateSearch(
    query: string,
    options: Omit<SearchOptions, 'offset'> = {}
  ): AsyncGenerator<SearchResult, void, unknown> {
    let offset = 0;
    const limit = options.limit ?? 20;

    while (true) {
      const result = await this.search(query, {
        ...options,
        limit,
        offset,
      });

      for (const item of result.items) {
        yield item;
      }

      if (!result.hasMore) break;
      offset = result.offset + result.limit;
    }
  }
}

// Re-export types
export * from './models';
export * from './errors';
```

### Node.js Usage Examples

```typescript
import { MemoryClient, ObservationKind, MemoryError } from '@los/memory';

async function main() {
  // Create client
  const client = new MemoryClient({
    profile: 'claude',
    timeoutMs: 30000,
  });

  try {
    // Add observation
    const obs = await client.addObservation(
      'API Design Decision',
      'Decided to use REST over GraphQL',
      {
        kind: ObservationKind.DECISION,
        tags: ['api', 'rest', 'graphql'],
      }
    );
    console.log(`Created observation ${obs.id}`);

    // Search
    const results = await client.search('API design', { limit: 10 });
    for (const result of results.items) {
      console.log(`${result.observation.title} (score: ${result.score})`);
    }

    // Pagination with generator
    for await (const result of client.paginateSearch('bug fix')) {
      console.log(result.observation.title);
    }

    // Sessions
    const session = await client.startSession({
      project: 'myapp',
      workingDir: process.cwd(),
    });
    console.log(`Started session ${session.id}`);

    // End session
    const ended = await client.endSession('Completed feature X');
    console.log(`Session lasted ${ended.endTime ? 'completed' : 'active'}`);

  } catch (error) {
    if (error instanceof MemoryError) {
      console.error(`Error [${error.code}]: ${error.message}`);
      if (error.suggestion) {
        console.error(`Suggestion: ${error.suggestion}`);
      }
    } else {
      throw error;
    }
  }
}

main();
```

---

## Common Design Principles Summary

### 1. CLI Wrapper Pattern

All SDKs wrap the los-memory CLI:
- Build command arguments from method parameters
- Execute CLI with `--json` flag
- Parse JSON response
- Return typed results

### 2. Error Handling

- Parse JSON error responses
- Map error codes to language-specific errors
- Include suggestion for fixing
- Provide original context for debugging

### 3. Configuration Hierarchy

1. Constructor parameters (highest priority)
2. Environment variables
3. Default values (lowest priority)

### 4. Response Types

All SDKs implement equivalent types:
- `Observation`, `Session`, `Checkpoint`
- `PaginatedResult<T>` with pagination helpers
- `SearchResult` with relevance score

### 5. Async Patterns

- Go: Standard `context.Context` for cancellation
- Rust: `async/await` with `tokio`
- Node.js: `Promise` and `async/await`

### 6. Type Safety

- Go: Strong typing with structs
- Rust: Full type safety with serde
- Node.js: TypeScript interfaces

### 7. Resource Management

- Go: Context cancellation
- Rust: Drop trait for cleanup
- Node.js: Process cleanup on exit

## SDK Version Compatibility

| SDK Version | CLI Version | Features |
|-------------|-------------|----------|
| 1.0.x | 1.0.x | Core operations, search, sessions |
| 1.1.x | 1.1.x | + Checkpoints, links |
| 2.0.x | 2.0.x | + Async streaming, bulk operations |
