#!/usr/bin/env python3
"""Claude Code hook: record Skill / MCP invocations of the current turn.

Usage (stdin = hook JSON payload):
    log.py prompt   # UserPromptSubmit -> start a new turn
    log.py tool     # PreToolUse       -> append a call
    log.py stop     # Stop             -> mark the turn finished

Every session accumulates into its own file under sessions/, and whichever
session was touched last is republished to state.json, which SkillMonitor.app
reads. Without that split, a prompt submitted in one session would take
ownership of tool calls made in another.
"""

import fcntl
import glob
import json
import os
import re
import sys
import time

DIR = os.path.expanduser("~/.claude/skill-monitor")
SESSIONS = os.path.join(DIR, "sessions")
STATE = os.path.join(DIR, "state.json")
LOCK = os.path.join(DIR, ".lock")
HISTORY = os.path.join(DIR, "history.jsonl")

MAX_CALLS = 60
MAX_HISTORY_BYTES = 2 * 1024 * 1024
SESSION_TTL = 24 * 3600
MAX_SESSION_FILES = 40


def now():
    return time.strftime("%H:%M:%S")


# UserPromptSubmit also fires for things the harness injects into the session
# (background-task completions, reminders, CI events). Those are not turns the
# user started, so they must not reset the island.
SYSTEM_ENVELOPES = (
    "<task-notification",
    "<system-reminder",
    "<ci-monitor-event",
    "<local-command-stdout",
    "<command-message",
    "[SYSTEM NOTIFICATION",
    "[Artifact comment sent to Claude]",
)


def is_injected(text):
    head = text.lstrip()
    if head.startswith(SYSTEM_ENVELOPES):
        return True
    return "SYSTEM NOTIFICATION - NOT USER INPUT" in text


def display_prompt(text):
    """Collapse whitespace; strip tags only when the prompt is tag-wrapped."""
    text = text.strip()
    if text.startswith("<"):
        text = re.sub(r"<[^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:160]


def read_event():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def classify(event):
    """Return a call record for Skill / mcp__* tools, else None."""
    tool = event.get("tool_name") or ""
    args = event.get("tool_input") or {}
    if tool == "Skill":
        return {"kind": "skill", "server": None, "name": args.get("skill") or "?"}
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        server = parts[1] if len(parts) > 1 else "?"
        inner = "__".join(parts[2:]) if len(parts) > 2 else ""
        return {"kind": "mcp", "server": server, "name": inner or server}
    return None


def session_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")
    return os.path.join(SESSIONS, safe + ".json")


def load_json(path):
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def publish(state):
    """Make this session the one the island shows."""
    save_json(STATE, state)


def prune_sessions():
    try:
        files = glob.glob(os.path.join(SESSIONS, "*.json"))
        cutoff = time.time() - SESSION_TTL
        files.sort(key=os.path.getmtime, reverse=True)
        for index, path in enumerate(files):
            if index >= MAX_SESSION_FILES or os.path.getmtime(path) < cutoff:
                os.remove(path)
    except Exception:
        pass


def append_history(record):
    try:
        if os.path.exists(HISTORY) and os.path.getsize(HISTORY) > MAX_HISTORY_BYTES:
            os.replace(HISTORY, HISTORY + ".1")
        with open(HISTORY, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tool"
    event = read_event()
    session_id = event.get("session_id") or "unknown"

    os.makedirs(SESSIONS, exist_ok=True)
    with open(LOCK, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            path = session_path(session_id)
            state = load_json(path)

            if mode == "prompt":
                if is_injected(event.get("prompt") or ""):
                    return
                state = {
                    "session": session_id,
                    "cwd": event.get("cwd"),
                    "prompt": display_prompt(event.get("prompt") or ""),
                    "status": "running",
                    "started": now(),
                    "updated": now(),
                    "calls": [],
                }
                save_json(path, state)
                publish(state)
                prune_sessions()
                return

            if mode == "stop":
                if not state:
                    return
                state["status"] = "done"
                state["updated"] = now()
                save_json(path, state)
                publish(state)
                return

            call = classify(event)
            if call is None:
                return

            # Hooks may have been installed mid-session: start a turn anyway.
            if not state:
                state = {
                    "session": session_id,
                    "cwd": event.get("cwd"),
                    "prompt": None,
                    "status": "running",
                    "started": now(),
                    "calls": [],
                }

            calls = state.get("calls") or []
            for existing in calls:
                same = (
                    existing.get("kind") == call["kind"]
                    and existing.get("server") == call["server"]
                    and existing.get("name") == call["name"]
                )
                if same:
                    existing["count"] = int(existing.get("count", 1)) + 1
                    existing["time"] = now()
                    break
            else:
                call["count"] = 1
                call["time"] = now()
                calls.append(call)

            state["calls"] = calls[-MAX_CALLS:]
            state["status"] = "running"
            state["updated"] = now()
            state["session"] = session_id
            state.setdefault("cwd", event.get("cwd"))
            save_json(path, state)
            publish(state)

            append_history({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "session": session_id,
                "cwd": event.get("cwd"),
                "kind": call["kind"],
                "server": call["server"],
                "name": call["name"],
            })
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
