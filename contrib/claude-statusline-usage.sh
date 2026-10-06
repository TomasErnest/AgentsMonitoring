#!/bin/sh
# Claude Code status line for agentsmon's Usage column: saves the status JSON Claude Code passes
# on stdin (it carries rate_limits: five_hour / seven_day used_percentage + resets_at) to the
# agentsmon state dir, then prints a short line with the model and both limit windows.
#
# Install: copy somewhere stable, chmod +x, and in ~/.claude/settings.json set
#   "statusLine": {"type": "command", "command": "/path/to/claude-statusline-usage.sh",
#                  "refreshInterval": 60}
out="${AGENTSMON_STATE:-$HOME/.local/state/agentsmon}/usage"
mkdir -p "$out"
tmp="$out/.claude-status.$$"
cat > "$tmp" && mv -f "$tmp" "$out/claude-status.json"
python3 - "$out/claude-status.json" <<'PY'
import json, sys, time
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("Claude"); sys.exit()
parts = [d.get("model", {}).get("display_name", "Claude")]
for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
    w = (d.get("rate_limits") or {}).get(key) or {}
    if w.get("used_percentage") is not None:
        r = w.get("resets_at")
        parts.append(f"{label} {w['used_percentage']:.0f}%" + (time.strftime(" ↻%H:%M", time.localtime(r)) if r else ""))
print(" · ".join(parts))
PY
