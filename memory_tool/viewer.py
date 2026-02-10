#!/usr/bin/env python3
"""Local-only web viewer for the memory tool with enhanced terminal-like interface."""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import from the new package structure
from memory_tool.database import ensure_fts, ensure_schema, connect_db
from memory_tool.utils import resolve_db_path
from memory_tool.operations import normalize_rows, run_search, run_timeline, run_get, run_list
from memory_tool.sessions import list_sessions
from memory_tool.utils import DEFAULT_PROFILE, PROFILE_CHOICES

# Create a simple namespace for compatibility
class MemModule:
    pass

mem = MemModule()
mem.DEFAULT_DB = resolve_db_path(DEFAULT_PROFILE, None)
mem.PROFILE_CHOICES = PROFILE_CHOICES
mem.DEFAULT_PROFILE = DEFAULT_PROFILE
mem.resolve_db_path = resolve_db_path
mem.connect_db = connect_db
mem.ensure_schema = ensure_schema
mem.ensure_fts = ensure_fts
mem.run_search = run_search
mem.run_timeline = run_timeline
mem.run_get = run_get
mem.run_list = run_list
mem.normalize_rows = normalize_rows
mem.asdict = lambda x: x.__dict__ if hasattr(x, '__dict__') else x
mem.list_sessions = list_sessions

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Memory Viewer</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
      margin: 0;
      background: #0f1115;
      color: #e6e6e6;
      height: 100vh;
      overflow: hidden;
    }
    .container {
      display: flex;
      height: 100vh;
    }
    /* Sidebar */
    .sidebar {
      width: 320px;
      background: #1a1d24;
      border-right: 1px solid #2c3340;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .sidebar-header {
      padding: 16px;
      border-bottom: 1px solid #2c3340;
    }
    .sidebar-header h1 {
      margin: 0 0 12px 0;
      font-size: 18px;
      color: #4a9eff;
    }
    .nav-tabs {
      display: flex;
      gap: 4px;
    }
    .nav-tab {
      flex: 1;
      padding: 8px;
      background: #252a33;
      border: none;
      color: #9ca3af;
      cursor: pointer;
      border-radius: 4px;
      font-size: 12px;
      transition: all 0.2s;
    }
    .nav-tab:hover { background: #2c3340; color: #e6e6e6; }
    .nav-tab.active { background: #4a9eff; color: white; }
    .search-box {
      padding: 12px;
      border-bottom: 1px solid #2c3340;
    }
    .search-box input {
      width: 100%;
      padding: 8px 12px;
      background: #252a33;
      border: 1px solid #2c3340;
      border-radius: 6px;
      color: #e6e6e6;
      font-size: 14px;
    }
    .search-box input:focus {
      outline: none;
      border-color: #4a9eff;
    }
    .list-container {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
    }
    .list-item {
      padding: 12px;
      margin-bottom: 4px;
      background: #252a33;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.2s;
      border-left: 3px solid transparent;
    }
    .list-item:hover { background: #2c3340; }
    .list-item.active {
      background: #2c3340;
      border-left-color: #4a9eff;
    }
    .list-item .title {
      font-weight: 500;
      margin-bottom: 4px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .list-item .meta {
      font-size: 12px;
      color: #6b7280;
      display: flex;
      gap: 8px;
    }
    .kind-badge {
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      text-transform: uppercase;
      font-weight: 600;
    }
    .kind-decision { background: #f59e0b; color: #1a1d24; }
    .kind-fix { background: #10b981; color: #1a1d24; }
    .kind-note { background: #6b7280; color: #e6e6e6; }
    .kind-incident { background: #ef4444; color: white; }
    /* Main content */
    .main {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .toolbar {
      padding: 12px 16px;
      background: #1a1d24;
      border-bottom: 1px solid #2c3340;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .toolbar button {
      padding: 8px 16px;
      background: #252a33;
      border: 1px solid #2c3340;
      border-radius: 6px;
      color: #e6e6e6;
      cursor: pointer;
      font-size: 13px;
      transition: all 0.2s;
    }
    .toolbar button:hover { background: #2c3340; }
    .toolbar button.primary { background: #4a9eff; border-color: #4a9eff; }
    .toolbar button.primary:hover { background: #3a8eef; }
    .toolbar .spacer { flex: 1; }
    .content {
      flex: 1;
      overflow-y: auto;
      padding: 24px;
    }
    .empty-state {
      text-align: center;
      padding: 60px;
      color: #6b7280;
    }
    .detail-card {
      background: #1a1d24;
      border: 1px solid #2c3340;
      border-radius: 8px;
      padding: 24px;
      max-width: 800px;
    }
    .detail-header {
      margin-bottom: 20px;
      padding-bottom: 16px;
      border-bottom: 1px solid #2c3340;
    }
    .detail-header h2 {
      margin: 0 0 12px 0;
      font-size: 24px;
      color: #f3f4f6;
    }
    .detail-meta {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      font-size: 14px;
      color: #9ca3af;
    }
    .detail-meta span { display: flex; align-items: center; gap: 4px; }
    .tags {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 16px 0;
    }
    .tag {
      padding: 4px 12px;
      background: #252a33;
      border-radius: 12px;
      font-size: 13px;
      color: #4a9eff;
    }
    .detail-section {
      margin-top: 20px;
    }
    .detail-section h3 {
      margin: 0 0 12px 0;
      font-size: 14px;
      text-transform: uppercase;
      color: #6b7280;
      letter-spacing: 0.5px;
    }
    .detail-section p {
      margin: 0;
      line-height: 1.6;
      color: #d1d5db;
    }
    .raw-content {
      background: #252a33;
      border-radius: 6px;
      padding: 16px;
      font-family: 'Monaco', 'Menlo', monospace;
      font-size: 13px;
      overflow-x: auto;
      white-space: pre-wrap;
      color: #a1a1aa;
    }
    .pager {
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 12px;
      border-top: 1px solid #2c3340;
    }
    .pager button {
      padding: 6px 12px;
      background: #252a33;
      border: 1px solid #2c3340;
      border-radius: 4px;
      color: #e6e6e6;
      cursor: pointer;
    }
    .pager button:disabled { opacity: 0.5; cursor: not-allowed; }
    .keyboard-help {
      position: fixed;
      bottom: 16px;
      right: 16px;
      background: #252a33;
      border: 1px solid #2c3340;
      border-radius: 8px;
      padding: 12px 16px;
      font-size: 12px;
      color: #6b7280;
    }
    .keyboard-help kbd {
      background: #1a1d24;
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid #2c3340;
    }
    /* Scrollbar styling */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #1a1d24; }
    ::-webkit-scrollbar-thumb { background: #2c3340; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #3c4350; }
  </style>
</head>
<body>
  <div class="container">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h1>🧠 Memory</h1>
        <div class="nav-tabs">
          <button class="nav-tab active" onclick="switchTab('observations')">Observations</button>
          <button class="nav-tab" onclick="switchTab('sessions')">Sessions</button>
        </div>
      </div>
      <div class="search-box">
        <input id="searchInput" placeholder="Search... (press / to focus)" onkeyup="handleSearch(event)" />
      </div>
      <div class="list-container" id="listContainer">
        <!-- List items will be rendered here -->
      </div>
      <div class="pager">
        <button onclick="prevPage()" id="prevBtn">← Prev</button>
        <span id="pageInfo">Page 1</span>
        <button onclick="nextPage()" id="nextBtn">Next →</button>
      </div>
    </aside>

    <main class="main">
      <div class="toolbar">
        <button onclick="refreshData()">🔄 Refresh</button>
        <button onclick="loadTimeline()">📅 Timeline</button>
        <button class="primary" onclick="startSession()">▶ Start Session</button>
        <div class="spacer"></div>
        <button onclick="exportData()">📤 Export</button>
      </div>
      <div class="content" id="content">
        <div class="empty-state">
          <h2>Select an item to view details</h2>
          <p>Use the sidebar to browse observations and sessions</p>
        </div>
      </div>
    </main>
  </div>

  <div class="keyboard-help">
    <kbd>j</kbd>/<kbd>k</kbd> navigate <kbd>Enter</kbd> view <kbd>/</kbd> search <kbd>r</kbd> refresh
  </div>

<script>
let currentTab = 'observations';
let currentPage = 0;
let pageSize = 20;
let items = [];
let selectedIndex = -1;
let tokenParam = new URLSearchParams(window.location.search).get('token');

async function api(path) {
  const sep = path.includes('?') ? '&' : '?';
  const url = tokenParam ? `${path}${sep}token=${encodeURIComponent(tokenParam)}` : path;
  const res = await fetch(url);
  return res.json();
}

function switchTab(tab) {
  currentTab = tab;
  currentPage = 0;
  selectedIndex = -1;
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  loadData();
}

async function loadData() {
  const container = document.getElementById('listContainer');
  container.innerHTML = '<div style="padding: 20px; text-align: center; color: #6b7280;">Loading...</div>';

  let data;
  if (currentTab === 'observations') {
    data = await api(`/api/list?limit=${pageSize}&offset=${currentPage * pageSize}`);
    items = data.results || [];
    renderObservationList(items);
  } else {
    data = await api(`/api/sessions?limit=${pageSize}&offset=${currentPage * pageSize}`);
    items = data.sessions || [];
    renderSessionList(items);
  }

  updatePager();
}

function renderObservationList(observations) {
  const container = document.getElementById('listContainer');
  if (!observations.length) {
    container.innerHTML = '<div style="padding: 20px; text-align: center; color: #6b7280;">No observations</div>';
    return;
  }

  container.innerHTML = observations.map((obs, i) => `
    <div class="list-item ${i === selectedIndex ? 'active' : ''}" onclick="selectItem(${i})" data-index="${i}">
      <div class="title">${escapeHtml(obs.title)}</div>
      <div class="meta">
        <span class="kind-badge kind-${obs.kind}">${obs.kind}</span>
        <span>${formatTime(obs.timestamp)}</span>
        <span>${obs.project}</span>
      </div>
    </div>
  `).join('');
}

function renderSessionList(sessions) {
  const container = document.getElementById('listContainer');
  if (!sessions.length) {
    container.innerHTML = '<div style="padding: 20px; text-align: center; color: #6b7280;">No sessions</div>';
    return;
  }

  container.innerHTML = sessions.map((session, i) => `
    <div class="list-item ${i === selectedIndex ? 'active' : ''}" onclick="selectItem(${i})" data-index="${i}">
      <div class="title">Session ${session.id}: ${escapeHtml(session.project)}</div>
      <div class="meta">
        <span class="kind-badge ${session.status === 'active' ? 'kind-fix' : 'kind-note'}">${session.status}</span>
        <span>${formatTime(session.start_time)}</span>
        <span>${session.agent_type}</span>
      </div>
    </div>
  `).join('');
}

function selectItem(index) {
  selectedIndex = index;
  document.querySelectorAll('.list-item').forEach((el, i) => {
    el.classList.toggle('active', i === index);
  });

  if (currentTab === 'observations') {
    showObservationDetail(items[index]);
  } else {
    showSessionDetail(items[index]);
  }
}

function showObservationDetail(obs) {
  const content = document.getElementById('content');
  const tags = (obs.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('');

  content.innerHTML = `
    <div class="detail-card">
      <div class="detail-header">
        <h2>${escapeHtml(obs.title)}</h2>
        <div class="detail-meta">
          <span>🆔 ID: ${obs.id}</span>
          <span>📁 ${obs.project}</span>
          <span>🏷️ ${obs.kind}</span>
          <span>🕐 ${formatFullTime(obs.timestamp)}</span>
          ${obs.session_id ? `<span>🔷 Session ${obs.session_id}</span>` : ''}
        </div>
      </div>
      ${tags ? `<div class="tags">${tags}</div>` : ''}
      <div class="detail-section">
        <h3>Summary</h3>
        <p>${escapeHtml(obs.summary)}</p>
      </div>
      ${obs.raw ? `
        <div class="detail-section">
          <h3>Raw Content</h3>
          <pre class="raw-content">${escapeHtml(obs.raw)}</pre>
        </div>
      ` : ''}
    </div>
  `;
}

function showSessionDetail(session) {
  const content = document.getElementById('content');
  content.innerHTML = `
    <div class="detail-card">
      <div class="detail-header">
        <h2>Session ${session.id}: ${escapeHtml(session.project)}</h2>
        <div class="detail-meta">
          <span>🔷 ID: ${session.id}</span>
          <span>📊 ${session.status}</span>
          <span>🤖 ${session.agent_type}</span>
          <span>🕐 ${formatFullTime(session.start_time)}</span>
        </div>
      </div>
      ${session.summary ? `
        <div class="detail-section">
          <h3>Summary</h3>
          <p>${escapeHtml(session.summary)}</p>
        </div>
      ` : ''}
      <div class="detail-section">
        <h3>Working Directory</h3>
        <p><code>${escapeHtml(session.working_dir)}</code></p>
      </div>
    </div>
  `;
}

function handleSearch(event) {
  if (event.key === 'Enter') {
    const query = event.target.value;
    if (query) {
      searchObservations(query);
    } else {
      loadData();
    }
  }
}

async function searchObservations(query) {
  const container = document.getElementById('listContainer');
  container.innerHTML = '<div style="padding: 20px; text-align: center; color: #6b7280;">Searching...</div>';

  const data = await api(`/api/search?query=${encodeURIComponent(query)}&limit=${pageSize}&offset=${currentPage * pageSize}`);
  items = data.results || [];
  renderObservationList(items);
  updatePager();
}

function updatePager() {
  document.getElementById('pageInfo').textContent = `Page ${currentPage + 1}`;
  document.getElementById('prevBtn').disabled = currentPage === 0;
}

function prevPage() {
  if (currentPage > 0) {
    currentPage--;
    loadData();
  }
}

function nextPage() {
  currentPage++;
  loadData();
}

function refreshData() {
  loadData();
}

async function loadTimeline() {
  const container = document.getElementById('content');
  const data = await api(`/api/timeline?limit=50`);
  const results = data.results || [];

  if (!results.length) {
    container.innerHTML = '<div class="empty-state"><h2>No timeline data</h2></div>';
    return;
  }

  // Group by day
  const byDay = {};
  results.forEach(obs => {
    const day = obs.timestamp.slice(0, 10);
    if (!byDay[day]) byDay[day] = [];
    byDay[day].push(obs);
  });

  container.innerHTML = `
    <div class="detail-card">
      <div class="detail-header">
        <h2>📅 Timeline View</h2>
        <div class="detail-meta">
          <span>${results.length} observations</span>
        </div>
      </div>
      ${Object.entries(byDay).sort().reverse().map(([day, obsList]) => `
        <div class="detail-section">
          <h3>${day}</h3>
          ${obsList.map(obs => `
            <div style="padding: 12px; background: #252a33; border-radius: 6px; margin-bottom: 8px; cursor: pointer;"
                 onclick="showObservationDetail(${JSON.stringify(obs).replace(/"/g, '&quot;')})">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <strong>${escapeHtml(obs.title)}</strong>
                <span class="kind-badge kind-${obs.kind}">${obs.kind}</span>
              </div>
              <div style="font-size: 12px; color: #6b7280; margin-top: 4px;">
                ${obs.timestamp.slice(11, 16)} • ${obs.project}
              </div>
            </div>
          `).join('')}
        </div>
      `).join('')}
    </div>
  `;
}

function startSession() {
  alert('Use CLI: memory_tool.py session start --project <name>');
}

function exportData() {
  alert('Use CLI: memory_tool.py share --output bundle.json');
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') {
    if (e.key === 'Escape') {
      e.target.blur();
    }
    return;
  }

  switch (e.key) {
    case 'j':
      if (selectedIndex < items.length - 1) selectItem(selectedIndex + 1);
      break;
    case 'k':
      if (selectedIndex > 0) selectItem(selectedIndex - 1);
      break;
    case 'Enter':
      if (selectedIndex >= 0) selectItem(selectedIndex);
      break;
    case '/':
      e.preventDefault();
      document.getElementById('searchInput').focus();
      break;
    case 'r':
      refreshData();
      break;
    case 'n':
      if (e.ctrlKey || e.metaKey) return;
      nextPage();
      break;
    case 'p':
      if (e.ctrlKey || e.metaKey) return;
      prevPage();
      break;
  }
});

// Utility functions
function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatTime(timestamp) {
  if (!timestamp) return '';
  return timestamp.slice(11, 16);
}

function formatFullTime(timestamp) {
  if (!timestamp) return '';
  return timestamp.replace('T', ' ').slice(0, 19);
}

// Initial load
loadData();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    db_path: str = mem.DEFAULT_DB
    auth_token: str | None = None

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, parsed) -> bool:
        token = self.auth_token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            if header[len("Bearer "):].strip() == token:
                return True
        query = parse_qs(parsed.query)
        query_token = query.get("token", [None])[0]
        if query_token == token:
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized(parsed):
            self._json({"ok": False, "error": "unauthorized"}, status=401)
            return
        if parsed.path == "/":
            self._html(HTML)
            return

        if parsed.path.startswith("/api/"):
            query = parse_qs(parsed.query)
            conn = None
            try:
                conn = mem.connect_db(self.db_path)
                mem.ensure_schema(conn)
                mem.ensure_fts(conn)

                if parsed.path == "/api/search":
                    search_query = query.get("query", [""])[0]
                    limit = int(query.get("limit", ["10"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    mode = query.get("mode", ["auto"])[0]
                    quote = query.get("quote", ["0"])[0] in {"1", "true", "yes"}
                    results = mem.run_search(
                        conn,
                        search_query,
                        limit,
                        offset=offset,
                        mode=mode,
                        quote=quote,
                    )
                    self._json({"ok": True, "results": results})
                    return

                if parsed.path == "/api/timeline":
                    around_id = query.get("around_id", [None])[0]
                    window_minutes = int(query.get("window_minutes", ["120"])[0])
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    start = query.get("start", [None])[0]
                    end = query.get("end", [None])[0]
                    around_val = int(around_id) if around_id else None
                    results = mem.run_timeline(
                        conn,
                        start,
                        end,
                        around_val,
                        window_minutes,
                        limit,
                        offset=offset,
                    )
                    self._json({"ok": True, "results": [mem.asdict(r) for r in results]})
                    return

                if parsed.path == "/api/get":
                    ids_raw = query.get("ids", [""])[0]
                    ids = [int(part.strip()) for part in ids_raw.split(",") if part.strip()]
                    results = mem.run_get(conn, ids)
                    self._json({"ok": True, "results": [mem.asdict(r) for r in results]})
                    return

                if parsed.path == "/api/list":
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    results = mem.run_list(conn, limit, offset=offset)
                    self._json({"ok": True, "results": [mem.asdict(r) for r in results]})
                    return

                if parsed.path == "/api/sessions":
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    sessions = mem.list_sessions(conn, limit=limit, offset=offset)
                    self._json({"ok": True, "sessions": [mem.asdict(s) for s in sessions]})
                    return

            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, status=500)
                return
            finally:
                if conn is not None:
                    conn.close()

        self.send_response(404)
        self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory tool viewer")
    parser.add_argument(
        "--profile",
        choices=mem.PROFILE_CHOICES,
        default=mem.DEFAULT_PROFILE,
        help="Memory profile to select default DB path",
    )
    parser.add_argument("--db", default=None, help="SQLite database path (overrides --profile)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=37777)
    parser.add_argument("--auth-token", default=None, help="Require a token for access")
    args = parser.parse_args()

    Handler.db_path = mem.resolve_db_path(args.profile, args.db)
    Handler.auth_token = args.auth_token
    server = HTTPServer((args.host, args.port), Handler)
    print(f"Memory viewer running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
