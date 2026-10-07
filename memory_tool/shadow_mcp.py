"""JSON-line stdio MCP for read-only shadow comparisons."""

import argparse
import json
import os
import sys
from .shadow import (DEFAULT_DB, connect, get, record_compare, search_detailed, status)


INSTRUCTIONS = (
    "This is a read-only los-memory shadow of Nowledge. Nowledge remains the primary for normal "
    "memory reads and all writes. Use shadow tools for explicit comparison and validation. Check "
    "verified_at and sync status; a shadow result may be stale, and it is an as-of snapshot, not "
    "current truth. Do not silently migrate, save here, or treat missing results as missing primary "
    "memories. shadow_search is literal substring matching, not semantic search; its reply includes "
    "`meta` describing which index path each term took (trigram / bigram / scan), any uncovered "
    "terms, and the project coverage behind a filter. An empty project-filtered result is not "
    "evidence that the project has no memories.\n"
    "WHEN TO USE IT: it is strongest where the query carries a literal anchor (ID, hash, path, error "
    "code, version, hostname, filename) or a short CJK term, when completeness matters more than "
    "conceptual recall, and when the primary is unreachable. It is useless for paraphrased or "
    "conceptual questions and for anything needing writes, revisions, threads or graph relations.\n"
    "FRESHNESS PRECONDITION: configured availability is not proof of a usable mirror. Check "
    "shadow_status (or the verified_at on a result) before relying on it, and on a degraded or "
    "stale-looking result fall back to the primary immediately rather than retrying.\n"
    "DURING THE COMPARISON PHASE call shadow_compare alongside your normal primary lookup. It "
    "records the divergence between the two backends. Answer from the primary: shadow_compare is "
    "instrumentation, not a source of answers."
)


def tool(name, description, properties, required):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False}}


TOOLS = [
    tool("shadow_search",
         "Literal Chinese/English substring search of the default-space shadow (not semantic). "
         "Returns {results, meta}: meta.mode is 'index+filter' or 'scan', meta.paths names the index "
         "path per term, and meta.coverage is present whenever a filter is applied. `project` only "
         "matches records a registry assigns a project to; check meta.coverage before reading an empty "
         "result as 'this project has no memories'.",
         {"query": {"type": "string", "minLength": 1, "maxLength": 500},
          "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
          "project": {"type": "string"},
          "kind": {"type": "string"}}, ["query"]),
    tool("shadow_get", "Read active canonical snapshot by original Nowledge memory ID.",
         {"source_id": {"type": "string", "minLength": 1}}, ["source_id"]),
    tool("shadow_status",
         "Show coverage, freshness, contract coverage, 24h metering and last sync errors before "
         "comparing results.", {}, []),
    tool("shadow_compare",
         "Measurement tool for the read-path rollover: returns the shadow's answer immediately and "
         "queues the same query so a background job can record how the shadow and the primary diverge. "
         "It is NOT a source of answers — keep answering from the primary. Use it alongside a normal "
         "memory lookup during the comparison phase. The primary side is deliberately not called here "
         "because it costs ~13 s per query.",
         {"query": {"type": "string", "minLength": 1, "maxLength": 500},
          "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}, ["query"]),
]


def dispatch(conn, request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in request:
        return None
    response = {"jsonrpc": "2.0", "id": request["id"]}
    method = request.get("method")
    params = request.get("params", {})
    try:
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18") else "2025-06-18"
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "los-memory-shadow", "version": "0.1.0"},
                      "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            definition = next((entry for entry in TOOLS if entry["name"] == name), None)
            if definition is None or not isinstance(arguments, dict):
                raise ValueError("Unknown tool or invalid arguments")
            schema = definition["inputSchema"]
            if set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
                raise ValueError("Unexpected or missing arguments")
            for key, value in arguments.items():
                if schema["properties"][key]["type"] == "string" and not isinstance(value, str):
                    raise ValueError("Expected string argument")
            if name == "shadow_search":
                rows, meta = search_detailed(conn, **arguments)
                payload = {"results": rows, "meta": meta}
            elif name == "shadow_get":
                if not arguments["source_id"] or len(arguments["source_id"]) > 512:
                    raise ValueError("Invalid source_id")
                payload = get(conn, **arguments)
            elif name == "shadow_compare":
                compare = record_compare(conn, arguments["query"],
                                         limit=arguments.get("limit", 10))
                payload = {"results": compare["results"], "meta": compare["meta"],
                           "comparison": {"class": compare["class"],
                                          "shadow_ids": compare["ids"],
                                          "queued": True,
                                          "note": "primary side is recorded asynchronously; "
                                                  "answer from the primary"}}
            else:
                payload = status(conn)
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                      "isError": False}
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
            return response
        response["result"] = result
    except (ValueError, TypeError, KeyError) as error:
        response["error"] = {"code": -32602, "message": str(error)}
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    os.umask(0o077)
    conn = connect(args.db)
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                result = dispatch(conn, request)
            except json.JSONDecodeError:
                result = {"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "Parse error"}}
            if result is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
