#!/usr/bin/env python3
"""Local-only web viewer for the memory tool."""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.append(str(Path(__file__).resolve().parent))
import memory_tool as mem  # noqa: E402

HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Memory Viewer</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #0f1115; color: #e6e6e6; }
    input, button, textarea { padding: 8px; margin: 4px; }
    .row { margin-bottom: 12px; }
    pre { background: #1b1f27; padding: 12px; overflow-x: auto; }
    .card { border: 1px solid #2c3340; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    .pager { display: inline-flex; gap: 8px; align-items: center; }
  </style>
</head>
<body>
  <h1>Memory Viewer</h1>
  <div class=\"row\">
    <input id=\"query\" placeholder=\"search query\" size=\"40\" />
    <select id=\"mode\">
      <option value=\"auto\">auto</option>
      <option value=\"fts\">fts</option>
      <option value=\"like\">like</option>
    </select>
    <label><input id=\"quote\" type=\"checkbox\" /> quote</label>
    <button onclick=\"doSearch()\">Search</button>
    <button onclick=\"doTimeline()\">Timeline (last 20)</button>
    <button onclick=\"doList()\">List (latest 20)</button>
  </div>
  <div class=\"row pager\">
    <label>Page size <input id=\"limit\" type=\"number\" value=\"20\" min=\"1\" style=\"width: 80px;\"></label>
    <button onclick=\"prevPage()\">Prev</button>
    <span id=\"page\">Page 1</span>
    <button onclick=\"nextPage()\">Next</button>
  </div>
  <div class=\"row\">
    <input id=\"ids\" placeholder=\"ids (1,2,3)\" size=\"20\" />
    <button onclick=\"doGet()\">Get</button>
  </div>
  <div id=\"results\"></div>

<script>
let currentAction = 'list';
let currentQuery = '';
let currentPage = 0;
const tokenParam = new URLSearchParams(window.location.search).get('token');

async function callApi(path) {
  const separator = path.includes('?') ? '&' : '?';
  const withToken = tokenParam ? `${path}${separator}token=${encodeURIComponent(tokenParam)}` : path;
  const res = await fetch(withToken);
  return res.json();
}

function renderResults(results) {
  const container = document.getElementById('results');
  container.innerHTML = '';
  results.forEach(item => {
    const tagText = Array.isArray(item.tags) ? item.tags.join(', ') : (item.tags || '');
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `<strong>#${item.id}</strong> ${item.title || ''}<br/>` +
      `<em>${item.timestamp || ''}</em><br/>` +
      `<code>${tagText}</code><br/>` +
      `<pre>${JSON.stringify(item, null, 2)}</pre>`;
    container.appendChild(div);
  });
}

function updatePageLabel() {
  document.getElementById('page').textContent = `Page ${currentPage + 1}`;
}

async function doSearch() {
  const query = document.getElementById('query').value;
  const mode = document.getElementById('mode').value;
  const quote = document.getElementById('quote').checked ? '1' : '0';
  const limit = document.getElementById('limit').value || '20';
  currentAction = 'search';
  currentQuery = query;
  currentPage = 0;
  const data = await callApi(`/api/search?query=${encodeURIComponent(query)}&mode=${encodeURIComponent(mode)}&quote=${quote}&limit=${limit}&offset=0`);
  renderResults(data.results || []);
  updatePageLabel();
}

async function doTimeline() {
  const limit = document.getElementById('limit').value || '20';
  currentAction = 'timeline';
  currentQuery = '';
  currentPage = 0;
  const data = await callApi(`/api/timeline?limit=${limit}&offset=0`);
  renderResults(data.results || []);
  updatePageLabel();
}

async function doList() {
  const limit = document.getElementById('limit').value || '20';
  currentAction = 'list';
  currentQuery = '';
  currentPage = 0;
  const data = await callApi(`/api/list?limit=${limit}&offset=0`);
  renderResults(data.results || []);
  updatePageLabel();
}

async function doGet() {
  const ids = document.getElementById('ids').value;
  currentAction = 'get';
  const data = await callApi(`/api/get?ids=${encodeURIComponent(ids)}`);
  renderResults(data.results || []);
  updatePageLabel();
}

async function nextPage() {
  if (currentAction === 'get') return;
  currentPage += 1;
  await loadPage();
}

async function prevPage() {
  if (currentAction === 'get') return;
  if (currentPage === 0) return;
  currentPage -= 1;
  await loadPage();
}

async function loadPage() {
  const limit = document.getElementById('limit').value || '20';
  const offset = currentPage * parseInt(limit, 10);
  if (currentAction === 'search') {
    const mode = document.getElementById('mode').value;
    const quote = document.getElementById('quote').checked ? '1' : '0';
    const data = await callApi(`/api/search?query=${encodeURIComponent(currentQuery)}&mode=${encodeURIComponent(mode)}&quote=${quote}&limit=${limit}&offset=${offset}`);
    renderResults(data.results || []);
  } else if (currentAction === 'timeline') {
    const data = await callApi(`/api/timeline?limit=${limit}&offset=${offset}`);
    renderResults(data.results || []);
  } else if (currentAction === 'list') {
    const data = await callApi(`/api/list?limit=${limit}&offset=${offset}`);
    renderResults(data.results || []);
  }
  updatePageLabel();
}
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
            if header[len("Bearer ") :].strip() == token:
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
