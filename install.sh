#!/bin/bash
# Build SkillMonitor and register its hooks with Claude Code.
# Safe to re-run: existing SkillMonitor hooks are replaced, others untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

PY=""
for candidate in /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    echo "error: python3 not found (install Xcode Command Line Tools: xcode-select --install)" >&2
    exit 1
fi

APP="$ROOT/dist/SkillMonitor.app"

if [ "${SKIP_BUILD:-0}" = "1" ] || ! command -v swiftc >/dev/null 2>&1; then
    if [ ! -d "$APP" ]; then
        echo "error: swiftc not found and no prebuilt app at dist/SkillMonitor.app" >&2
        echo "       either install the Xcode Command Line Tools:" >&2
        echo "         xcode-select --install" >&2
        echo "       or drop a release build in place:" >&2
        echo "         mkdir -p dist && unzip ~/Downloads/SkillMonitor-*.zip -d dist" >&2
        exit 1
    fi
    echo "==> using existing dist/SkillMonitor.app (skipping build)"
    xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
else
    echo "==> building"
    "$ROOT/build.sh"
fi

echo "==> registering hooks"
"$PY" - "$ROOT" "$PY" <<'PYEOF'
import json, os, sys

root, py = sys.argv[1], sys.argv[2]
cmd = "%s %s" % (py, os.path.join(root, "hook", "log.py"))
path = os.path.expanduser("~/.claude/settings.json")

settings = {}
if os.path.exists(path):
    with open(path) as fh:
        try:
            settings = json.load(fh)
        except ValueError:
            print("error: ~/.claude/settings.json is not valid JSON; fix it first")
            raise SystemExit(1)
    backup = path + ".bak-skillmonitor"
    if not os.path.exists(backup):
        with open(backup, "w") as fh:
            json.dump(settings, fh, indent=2, ensure_ascii=False)
        print("    backed up existing settings to %s" % backup)
else:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def ours(group):
    return any("skill-monitor" in (h.get("command") or "")
               for h in group.get("hooks", []))


hooks = settings.setdefault("hooks", {})
for event in list(hooks):
    hooks[event] = [g for g in hooks[event] if not ours(g)]
    if not hooks[event]:
        del hooks[event]

TOOLS = "Skill|mcp__.*"
hooks.setdefault("UserPromptSubmit", []).append(
    {"hooks": [{"type": "command", "command": cmd + " prompt"}]})
hooks.setdefault("PreToolUse", []).append(
    {"matcher": TOOLS,
     "hooks": [{"type": "command", "command": cmd + " tool", "async": True}]})
hooks.setdefault("PostToolUse", []).append(
    {"matcher": TOOLS,
     "hooks": [{"type": "command", "command": cmd + " done", "async": True}]})
hooks.setdefault("Stop", []).append(
    {"hooks": [{"type": "command", "command": cmd + " stop", "async": True}]})

with open(path, "w") as fh:
    json.dump(settings, fh, indent=2, ensure_ascii=False)
print("    4 hooks registered in %s" % path)
PYEOF

echo "==> launching"
open "$APP"

cat <<MSG

Done. The island hangs under the notch while Claude is frontmost.

  Show it for a terminal too   edit ~/.claude/skill-monitor/config.json
  Launch at login              System Settings > General > Login Items > +
                               $ROOT/dist/SkillMonitor.app
  Remove                       $ROOT/uninstall.sh

Hooks apply to sessions started from now on. Existing sessions pick them up
when Claude Code reloads its settings.
MSG
