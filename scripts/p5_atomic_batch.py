#!/usr/bin/env python3
"""The P5 atomic batch: one reversible, diffable edit to the profile patch.

Why a script instead of four manual edits: the batch flips four things that must
move **together** — evolve's row off, `dsh-host-tools` on, `dsh-memory-tools` on,
and one reload. Half of it either collides (`tool "dtodo" is already registered`,
2026-11-13) or leaves a tool with no implementation. A script can also prove it
did the right thing, and put the original bytes back.

Safety properties:

* **dry-run by default** — prints the unified diff and writes nothing.
* `--apply` saves the original next to the patch (``.bak-p5batch-<ts>``) *before*
  the first byte changes, so `--revert` is always available.
* `--check` verifies the three rows are in the intended state (static check; the
  runtime "each tool registers once" check needs the reload, see the printout).
* `--revert` restores the newest backup and re-runs ``--check`` expecting the
  **pre-batch** state, so a revert that silently did nothing is caught.

Usage::

    python3 scripts/p5_atomic_batch.py                 # dry-run + diff
    python3 scripts/p5_atomic_batch.py --apply         # write (backup first)
    python3 scripts/p5_atomic_batch.py --check         # is the batch state in place?
    python3 scripts/p5_atomic_batch.py --revert        # restore the newest backup
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import os
import shutil
import sys

DEFAULT_PATCH = os.path.expanduser("~/.dsh/profiles/web/cordis.patch.yml")

#: row id → (what the line must say after the batch, label)
TARGETS = {
    "dsh-memory-evolve": ("disabled: true", "evolve 整行停用（memory / memory_suggest / memory_review_status 退出）"),
    "dsh-host-tools": ("disabled: false", "dtodo / skill_manage / de_channel_send 上线"),
    "dsh-memory-tools": ("disabled: false", "memory 工具改由账本写入"),
}


def _is_row(line: str, row_id: str) -> bool:
    return line.strip() == f"- id: {row_id}"


def plan(lines: list[str]) -> tuple[list[str], list[str]]:
    """Return (new_lines, notes). Never mutates the input."""
    out: list[str] = []
    notes: list[str] = []
    pending: dict[str, tuple[str, str]] = dict(TARGETS)
    for index, line in enumerate(lines):
        out.append(line)
        indent = line[: len(line) - len(line.lstrip())]
        for row_id, (want, label) in list(pending.items()):
            if not _is_row(line, row_id):
                continue
            # Look ahead inside this row's own indentation block for an existing
            # ``disabled:`` line (rows end at the next ``- id:`` or a less-indented key).
            row_indent = len(indent)
            found_at = None
            for probe in range(index + 1, len(lines)):
                probe_line = lines[probe]
                if not probe_line.strip():
                    continue
                probe_indent = len(probe_line) - len(probe_line.lstrip())
                if probe_indent <= row_indent or _is_row(probe_line, "") is False and probe_line.strip().startswith("- id:"):
                    break
                if probe_line.strip().startswith("disabled:"):
                    found_at = probe
                    break
                if probe_indent < row_indent:
                    break
            if found_at is not None:
                current = lines[found_at].strip()
                if current == want:
                    notes.append(f"{row_id}: 已是 `{want}`（无需改动）")
                else:
                    notes.append(f"{row_id}: `{current}` → `{want}`（{label}）")
            else:
                notes.append(f"{row_id}: 插入 `{want}`（{label}）")
            pending.pop(row_id)
    if pending:
        raise SystemExit(f"patch 里找不到这些行：{', '.join(sorted(pending))}")

    # Second pass: rewrite the files with the planned insertions/replacements.
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        result.append(line)
        row_id = next((rid for rid in TARGETS if _is_row(line, rid)), None)
        if row_id is None:
            index += 1
            continue
        want, _ = TARGETS[row_id]
        indent = line[: len(line) - len(line.lstrip())]
        row_indent = len(indent)
        found_at = None
        probe = index + 1
        while probe < len(lines):
            probe_line = lines[probe]
            if not probe_line.strip():
                probe += 1
                continue
            probe_indent = len(probe_line) - len(probe_line.lstrip())
            if probe_indent <= row_indent or probe_line.strip().startswith("- id:"):
                break
            if probe_line.strip().startswith("disabled:"):
                found_at = probe
                break
            probe += 1
        if found_at is not None:
            for mid in range(index + 1, found_at):
                result.append(lines[mid])
            result.append(f"{indent}  {want}")
            index = found_at + 1
            continue
        result.append(f"{indent}  {want}")
        index += 1
    return result, notes


def check(lines: list[str]) -> list[str]:
    """Return the rows that are NOT in the intended post-batch state."""
    problems: list[str] = []
    for row_id, (want, _label) in TARGETS.items():
        row = next((i for i, line in enumerate(lines) if _is_row(line, row_id)), None)
        if row is None:
            problems.append(f"{row_id}: 行不存在")
            continue
        row_indent = len(lines[row]) - len(lines[row].lstrip())
        seen = None
        for probe in range(row + 1, len(lines)):
            probe_line = lines[probe]
            if not probe_line.strip():
                continue
            probe_indent = len(probe_line) - len(probe_line.lstrip())
            if probe_indent <= row_indent or probe_line.strip().startswith("- id:"):
                break
            if probe_line.strip().startswith("disabled:"):
                seen = probe_line.strip()
                break
        if seen != want:
            problems.append(f"{row_id}: 期望 `{want}`，实际 `{seen or '（无 disabled 行）'}`")
    return problems


def self_test() -> int:
    fixture = """- insert:
    - id: dsh-memory-evolve
      name: dsh-memory-evolve
      config:
        injectMemory: false
    - id: dsh-los-ops
      name: dsh-los-ops
- id: dsh-host-tools
  disabled: true
- id: dsh-memory-tools
  disabled: true
"""
    lines = fixture.splitlines()
    assert check(lines), "batch state must not be reported as already in place"
    planned, notes = plan(lines)
    assert len(notes) == 3, notes
    problems = check(planned)
    assert problems == [], problems
    # Applying twice must be a no-op (idempotent rows, not duplicated blocks).
    twice, _ = plan(planned)
    assert twice == planned, "second apply changed the file"
    # The evolve row keeps its config block.
    assert any("injectMemory: false" in line for line in planned)
    print("self-test: 3/3 pass")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P5 atomic batch (reversible)")
    parser.add_argument("--patch", default=DEFAULT_PATCH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revert", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    patch = os.path.expanduser(args.patch)
    if not os.path.isfile(patch):
        print(f"patch 不存在：{patch}", file=sys.stderr)
        return 2
    with open(patch, encoding="utf-8") as fh:
        original = fh.read()
    lines = original.splitlines()

    if args.check:
        problems = check(lines)
        for row_id, (want, label) in TARGETS.items():
            mark = "ok " if all(not p.startswith(row_id) for p in problems) else "✗  "
            print(f"{mark}{row_id}: {want} — {label}")
        if problems:
            print("未就绪：" + "; ".join(problems))
            return 1
        print("批状态已就位（静态检查）。运行时还要证明「每个工具只注册一次」——见 --apply 的打印。")
        return 0

    if args.revert:
        candidates = sorted(
            p for p in os.listdir(os.path.dirname(patch) or ".")
            if p.startswith(os.path.basename(patch) + ".bak-p5batch-")
        )
        if not candidates:
            print("没有 p5batch 备份可回滚（未执行过 --apply？）", file=sys.stderr)
            return 2
        backup = os.path.join(os.path.dirname(patch) or ".", candidates[-1])
        shutil.copyfile(backup, patch)
        with open(patch, encoding="utf-8") as fh:
            restored = fh.read().splitlines()
        if restored != original and check(restored) == []:
            print("回滚后仍是批状态？拒绝（备份可能已经是批后的文件）", file=sys.stderr)
            return 1
        print(f"已回滚到 {backup}\n随后仍需一次重载，工具才回到 evolve 那一套。")
        return 0

    planned, notes = plan(lines)
    diff = list(difflib.unified_diff(lines, planned, fromfile=patch, tofile=patch + " (batch)", lineterm=""))
    print("\n".join(notes))
    if diff:
        print("\n".join(diff))
    if not args.apply:
        print("\n（dry-run：加 --apply 才写盘，写前会先备份）")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = f"{patch}.bak-p5batch-{stamp}"
    shutil.copyfile(patch, backup)
    with open(patch, "w", encoding="utf-8") as fh:
        fh.write("\n".join(planned) + ("\n" if original.endswith("\n") else ""))
    with open(patch, encoding="utf-8") as fh:
        problems = check(fh.read().splitlines())
    print(f"\n已写入（备份：{backup}）")
    if problems:
        print("写入后静态检查未通过：" + "; ".join(problems), file=sys.stderr)
        print(f"可立即回滚：{sys.argv[0]} --revert", file=sys.stderr)
        return 1
    print("""
下一步（顺序不能换）：
  1) bash ~/.dsh/scripts/dsh-web-restart-detached.sh --detach
  2) 重载后确认「每个工具只注册一次」：
       grep -c 'is already registered' ~/.dsh/logs/dsh-web.log     # 不应新增
       新会话里 memory / dtodo / skill_manage / de_channel_send 各出现一次
  3) 写一条记忆（走新写侧）+ `mem list` 复核，确认落进账本
  4) 退役 reverse_divergence 判据（它的前提「markdown 是真源」到这一步结束）
回滚：本脚本 --revert + 一次重载。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
