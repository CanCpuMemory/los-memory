#!/bin/bash
# dw-observe-run.sh — dual-write 观测判据的定时承载脚本（launchd 用；人工也可直接跑）。
#
# 为什么要这层壳（而不是让 plist 直接调 python）：
#   1. **落账**：每次判定一行 append 到 ~/.dsh/memory-inject/dw-YYYY-MM.jsonl。
#      判活只看这个台账的最新行，不看 out.log（沿用 memory-inject-observe 的教训）。
#   2. **失败有人收**：rc≠0 时按小时幂等推飞书（同 key 不刷屏；跨小时才重发）。
#   3. **三态透传**：0 通过 / 1 真实漂移 / 2 判不了（2 不当 1，也不当 0）。
#
# 判据定义见 docs/design/evolve-replacement-plan.md §7.4；
# 判据实现见 scripts/dual_write_observe.py（--self-test 有 20 条纯函数控制）。
#
# 用法：bash scripts/dw-observe-run.sh [--window-hours N]   （N 目前未用，保留给趋势统计）
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LEDGER_DIR="$HOME/.dsh/memory-inject"
OUT="$LEDGER_DIR/dw-$(date +%Y-%m).jsonl"
PYTHON="/usr/bin/python3"

mkdir -p "$LEDGER_DIR"

TMP="$(mktemp -t los-memory-dw-observe)"
trap 'rm -f "$TMP"' EXIT

"$PYTHON" "$REPO/scripts/dual_write_observe.py" --json >"$TMP" 2>&1
RC=$?

# 落账：一行一次判定。输出不是 JSON 也要留一行——沉默是结论。
"$PYTHON" - "$TMP" "$OUT" "$RC" <<'PY'
import datetime, json, sys

tmp, out, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
raw = open(tmp, encoding="utf-8", errors="replace").read()
try:
    data = json.loads(raw)
except Exception:
    data = {"verdict": "unjudgeable", "rc": rc, "reason": "判据输出不是 JSON：" + raw[:300]}
row = {
    "recordedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "rc": rc,
    "verdict": data.get("verdict"),
    "reason": data.get("reason"),
    "failures": data.get("failures") or [],
    "unjudgeable": data.get("unjudgeable") or [],
    "checks": data.get("checks") or [],
    "metrics": data.get("metrics") or {},
}
with open(out, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("verdict=%s rc=%d failures=%s na=%d" % (
    row["verdict"], rc, "; ".join(row["failures"]) or "-", len(row["unjudgeable"])))
PY

if [ "$RC" -ne 0 ]; then
  MSG="$("$PYTHON" - "$TMP" "$RC" <<'PY'
import json, sys
tmp, rc = sys.argv[1], sys.argv[2]
try:
    d = json.loads(open(tmp, encoding='utf-8', errors='replace').read())
except Exception as exc:
    print(f"los-memory 双写判据判不了（rc={rc}）：{exc}")
    raise SystemExit
lines = [f"los-memory 双写判据 {d.get('verdict')}（rc={rc}）"]
for row in d.get("checks") or []:
    if row.get("state") != "pass":
        lines.append(f"- {row.get('name')}: {row.get('state')} — {row.get('detail')}")
if len(lines) == 1:
    lines.append(d.get("reason") or "见台账")
print("\n".join(lines)[:1200])
PY
)"
  KEY="los-memory-dw-observe-$(date +%Y%m%d%H)"
  "$HOME/.dsh/scripts/feishu-push.sh" --key "$KEY" --max-chars 1200 "$MSG" >/dev/null 2>&1 || true
fi

exit "$RC"
