#!/usr/bin/env python3
"""Install/remove only this adapter's hooks; never alter hook trust."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hook"))
from codex import EVENTS, load, save

MARKER = "skillmonitor-codex-v1"


def strict_json(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(str(path) + " must contain a JSON object")
    return value


def ours(handler):
    try:
        tokens = shlex.split(handler.get("command", ""))
        i = tokens.index("--monitor-id")
        return tokens[i + 1] == MARKER
    except (ValueError, IndexError, AttributeError):
        return False


def without_ours(config):
    result = copy.deepcopy(config)
    hooks = result.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            raise ValueError("hook event must be an array: " + event)
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("invalid hook matcher group: " + event)
            remaining = [h for h in group["hooks"] if not ours(h)]
            if remaining == group["hooks"]:
                kept.append(group)
            elif remaining:
                group["hooks"] = remaining
                kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        result.pop("hooks", None)
    return result


def write_back(path, old, new):
    if old == new and path.exists():
        return
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak-skillmonitor-" + str(time.time_ns())))
    save(path, new)


def setup(home, uninstall=False):
    hooks_path = home / "hooks.json"
    ui_path = Path.home() / ".config/skill-monitor/config.json"
    # Parse everything before writing anything. Malformed settings are never reset.
    old = strict_json(hooks_path)
    new = without_ours(old)
    ui_old = strict_json(ui_path)
    ui = copy.deepcopy(ui_old)
    if not uninstall:
        legacy = strict_json(Path.home() / ".claude/skill-monitor/config.json")
        state_files = ui.setdefault("stateFiles", [str(Path.home() / ".claude/skill-monitor/state.json")])
        hosts = ui.setdefault("showWhenFrontmost", legacy.get("showWhenFrontmost", ["com.anthropic.claudefordesktop"]))
        if not isinstance(state_files, list) or not all(isinstance(x, str) for x in state_files):
            raise ValueError("stateFiles must be an array of paths")
        if not isinstance(hosts, list) or not all(isinstance(x, str) for x in hosts):
            raise ValueError("showWhenFrontmost must be an array of bundle IDs")
        state_path = str(home / "skill-monitor/state.json")
        if state_path not in state_files:
            state_files.append(state_path)
        for bundle in ("com.apple.Terminal", "com.googlecode.iterm2"):
            if bundle not in hosts:
                hosts.append(bundle)
        target = home / "skill-monitor/bin/codex.py"
        command = shlex.join([sys.executable, str(target), "--codex-home", str(home), "--monitor-id", MARKER])
        for event in sorted(EVENTS):
            # Synchronous local writes keep start/end ordering deterministic.
            # Tight timeout; no stdout instructions, no approval decisions.
            group = {"hooks": [{"type": "command", "command": command, "timeout": 3}]}
            if event in ("PreToolUse", "PostToolUse"):
                group["matcher"] = "^(Bash|exec_command|shell_command|mcp__.*)$"
            new.setdefault("hooks", {}).setdefault(event, []).append(group)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        content = (Path(__file__).resolve().parents[1] / "hook/codex.py").read_bytes()
        # Write code atomically too; do not modify existing trust records.
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".install-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        write_back(ui_path, ui_old, ui)
    write_back(hooks_path, old, new)
    print(("Removed" if uninstall else "Registered") + " SkillMonitor Codex hooks: " + str(hooks_path))
    if uninstall:
        print("History, adapter and display configuration kept; Claude hooks are untouched.")
    else:
        print("In Codex CLI, open /hooks and review/trust the seven SkillMonitor hooks.")
        print("Start a new conversation. Installation alone does not mean hooks are running.")


def status(home):
    config = strict_json(home / "hooks.json")
    registered = [event for event, groups in config.get("hooks", {}).items()
                  if any(ours(h) for g in groups for h in g.get("hooks", []))]
    state = load(home / "skill-monitor/state.json")
    print("Registered events: " + (", ".join(sorted(registered)) or "none"))
    print("Adapter file: " + ("present" if (home / "skill-monitor/bin/codex.py").is_file() else "missing"))
    print("Observed event: " + (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["updated_at"]))
                                if state.get("updated_at") else "none"))
    print("Trust/runtime support: verify in Codex /hooks (not inferred from registration).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    home = (args.codex_home or Path(os.environ.get("CODEX_HOME", "~/.codex"))).expanduser().absolute()
    try:
        status(home) if args.status else setup(home, args.uninstall)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, "SkillMonitor setup failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
