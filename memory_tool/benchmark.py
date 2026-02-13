#!/usr/bin/env python3
"""Simple benchmark for memory_tool CLI latency."""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run_cmd(cmd: list[str]) -> tuple[float, int, str]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, proc.returncode, proc.stdout + proc.stderr


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    idx = max(0, min(len(values) - 1, int(round((p / 100.0) * (len(values) - 1)))))
    sorted_values = sorted(values)
    return sorted_values[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark memory_tool CLI latency")
    parser.add_argument("--python", default=sys.executable, help="Python executable")
    parser.add_argument("--iterations", type=int, default=20, help="Iterations per operation")
    parser.add_argument("--profile", default="shared", choices=["codex", "claude", "shared"])
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="los-memory-bench-") as tmp:
        db_path = str(Path(tmp) / "bench.db")
        tool_path = str(Path(__file__).resolve().parent / "memory_tool.py")
        base = [args.python, tool_path, "--db", db_path]

        # Warm-up DB
        init_elapsed, init_code, init_out = run_cmd(base + ["init"])
        if init_code != 0:
            print(json.dumps({"ok": False, "error": "init_failed", "output": init_out}, ensure_ascii=False, indent=2))
            sys.exit(1)

        # Seed one record for search/list
        seed = base + [
            "add",
            "--project",
            "bench",
            "--kind",
            "note",
            "--title",
            "seed",
            "--summary",
            "benchmark seed data",
            "--tags",
            "bench,seed,tenant:default,user:bench",
            "--raw",
            "seed",
        ]
        _, seed_code, seed_out = run_cmd(seed)
        if seed_code != 0:
            print(json.dumps({"ok": False, "error": "seed_failed", "output": seed_out}, ensure_ascii=False, indent=2))
            sys.exit(1)

        add_latencies: list[float] = []
        search_latencies: list[float] = []
        list_latencies: list[float] = []

        for i in range(args.iterations):
            add_cmd = base + [
                "add",
                "--project",
                "bench",
                "--kind",
                "note",
                "--title",
                f"bench-{i}",
                "--summary",
                "latency sample",
                "--tags",
                "bench,tenant:default,user:bench",
                "--raw",
                "sample",
            ]
            elapsed, code, out = run_cmd(add_cmd)
            if code != 0:
                print(json.dumps({"ok": False, "error": "add_failed", "output": out}, ensure_ascii=False, indent=2))
                sys.exit(1)
            add_latencies.append(elapsed)

            search_cmd = base + [
                "search",
                "latency",
                "--limit",
                "10",
                "--require-tags",
                "tenant:default,user:bench",
            ]
            elapsed, code, out = run_cmd(search_cmd)
            if code != 0:
                print(json.dumps({"ok": False, "error": "search_failed", "output": out}, ensure_ascii=False, indent=2))
                sys.exit(1)
            search_latencies.append(elapsed)

            list_cmd = base + [
                "list",
                "--limit",
                "10",
                "--require-tags",
                "tenant:default,user:bench",
            ]
            elapsed, code, out = run_cmd(list_cmd)
            if code != 0:
                print(json.dumps({"ok": False, "error": "list_failed", "output": out}, ensure_ascii=False, indent=2))
                sys.exit(1)
            list_latencies.append(elapsed)

        def summarize(name: str, values: list[float]) -> dict:
            return {
                "op": name,
                "count": len(values),
                "avg_ms": round(statistics.mean(values), 2),
                "p50_ms": round(percentile(values, 50), 2),
                "p95_ms": round(percentile(values, 95), 2),
                "max_ms": round(max(values), 2),
            }

        report = {
            "ok": True,
            "iterations": args.iterations,
            "init_ms": round(init_elapsed, 2),
            "results": [
                summarize("add", add_latencies),
                summarize("search", search_latencies),
                summarize("list", list_latencies),
            ],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
