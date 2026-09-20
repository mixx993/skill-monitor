#!/bin/bash
# Remove SkillMonitor hooks from ~/.claude/settings.json and stop the app.
set -euo pipefail
pkill -f "SkillMonitor.app/Contents/MacOS/SkillMonitor" 2>/dev/null || true
/usr/bin/python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude/settings.json")
s = json.load(open(p))
hooks = s.get("hooks", {})
for event in list(hooks):
    hooks[event] = [g for g in hooks[event]
                    if not any("skill-monitor" in (h.get("command") or "")
                               for h in g.get("hooks", []))]
    if not hooks[event]:
        del hooks[event]
if not hooks:
    s.pop("hooks", None)
json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
print("hooks removed")
PY
echo "state kept at ~/.claude/skill-monitor (delete it manually if you want)"
