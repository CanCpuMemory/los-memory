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
  </style>
</head>
<body>
  <h1>Memory Viewer</h1>
  <div class=\"row\">
    <input id=\"query\" placeholder=\"search query\" size=\"40\" />
    <button onclick=\"doSearch()\">Search</button>
    <button onclick=\"doTimeline()\">Timeline (last 20)</button>
  </div>
  <div class=\"row\">
    <input id=\"ids\" placeholder=\"ids (1,2,3)\" size=\"20\" />
    <button onclick=\"doGet()\">Get</button>
  </div>
  <div id=\"results\"></div>

<script>
async function callApi(path) {
  const res = await fetch(path);
  return res.json();
}

function renderResults(results) {
  const container = document.getElementById('results');
  container.innerHTML = '';
  results.forEach(item => {
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `<strong>#${item.id}</strong> ${item.title || ''}<br/>` +
      `<em>${item.timestamp || ''}</em><br/>` +
      `<code>${item.tags || ''}</code><br/>` +
      `<pre>${JSON.stringify(item, null, 2)}</pre>`;
    container.appendChild(div);
  });
}

async function doSearch() {
  const query = document.getElementById('query').value;
  const data = await callApi(`/api/search?query=${encodeURIComponent(query)}`);
  renderResults(data.results || []);
}

async function doTimeline() {
  const data = await callApi('/api/timeline?limit=20');
  renderResults(data.results || []);
}

async function doGet() {
  const ids = document.getElementById('ids').value;
  const data = await callApi(`/api/get?ids=${encodeURIComponent(ids)}`);
  renderResults(data.results || []);
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    db_path: str = mem.DEFAULT_DB

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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(HTML)
            return

        if parsed.path.startswith("/api/"):
            query = parse_qs(parsed.query)
            try:
                conn = mem.connect_db(self.db_path)
                mem.ensure_schema(conn)
                mem.ensure_fts(conn)

                if parsed.path == "/api/search":
                    search_query = query.get("query", [""])[0]
                    limit = int(query.get("limit", ["10"])[0])
                    results = mem.run_search(conn, search_query, limit)
                    self._json({"ok": True, "results": results})
                    return

                if parsed.path == "/api/timeline":
                    around_id = query.get("around_id", [None])[0]
                    window_minutes = int(query.get("window_minutes", ["120"])[0])
                    limit = int(query.get("limit", ["20"])[0])
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
                    )
                    self._json({"ok": True, "results": [mem.asdict(r) for r in results]})
                    return

                if parsed.path == "/api/get":
                    ids_raw = query.get("ids", [""])[0]
                    ids = [int(part.strip()) for part in ids_raw.split(",") if part.strip()]
                    results = mem.run_get(conn, ids)
                    self._json({"ok": True, "results": [mem.asdict(r) for r in results]})
                    return
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, status=500)
                return

        self.send_response(404)
        self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory tool viewer")
    parser.add_argument("--db", default=mem.DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=37777)
    args = parser.parse_args()

    Handler.db_path = args.db
    server = HTTPServer((args.host, args.port), Handler)
    print(f"Memory viewer running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
