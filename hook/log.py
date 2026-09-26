#!/usr/bin/env python3
"""Claude Code hook: record Skill / MCP invocations of the current turn.

Usage (stdin = hook JSON payload):
    log.py prompt   # UserPromptSubmit -> start a new turn
    log.py tool     # PreToolUse       -> append a call
    log.py done     # PostToolUse      -> attach the call's duration
    log.py stop     # Stop             -> mark the turn finished
    log.py instr    # InstructionsLoaded -> note an instruction file mid-session

Every session accumulates into its own file under sessions/, and whichever
session was touched last is republished to state.json, which SkillMonitor.app
reads. Without that split, a prompt submitted in one session would take
ownership of tool calls made in another.
"""

import fcntl
import glob
import hashlib
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
# Last fingerprint seen per instruction file, across all sessions, and the log
# of every time one of them appeared, changed or vanished.
FINGERPRINTS = os.path.join(DIR, "fingerprints.json")
INSTRUCTION_LOG = os.path.join(DIR, "instructions.jsonl")

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
    "<command-name",
    "[SYSTEM NOTIFICATION",
    "[Artifact comment sent to Claude]",
)


def is_injected(text):
    head = text.lstrip()
    if head.startswith(SYSTEM_ENVELOPES):
        return True
    return "SYSTEM NOTIFICATION - NOT USER INPUT" in text


COMMAND_TAG = re.compile(r"<command-name>\s*/?([A-Za-z0-9:_.-]+)\s*</command-name>")
BARE_SLASH = re.compile(r"^/([A-Za-z0-9:_.-]+)")


def slash_command(text):
    """The command name in `/name ...` or a <command-name> envelope."""
    match = COMMAND_TAG.search(text)
    if match:
        return match.group(1)
    match = BARE_SLASH.match(text.lstrip())
    return match.group(1) if match else None


def is_known_skill(name, cwd):
    """True when a SKILL.md exists for this name, so /model and friends
    are not mistaken for skills."""
    base = name.split(":")[-1]
    candidates = [
        os.path.expanduser("~/.claude/skills/%s/SKILL.md" % base),
        os.path.expanduser("~/.claude/skills/synced/%s/SKILL.md" % base),
    ]
    if cwd:
        candidates.append(os.path.join(cwd, ".claude", "skills", base, "SKILL.md"))
    if any(os.path.exists(c) for c in candidates):
        return True
    plugin_glob = os.path.expanduser("~/.claude/plugins/**/skills/%s/SKILL.md" % base)
    try:
        return bool(glob.glob(plugin_glob, recursive=True))
    except Exception:
        return False


# --- instruction files (CLAUDE.md, memory, settings) ------------------------
# These steer every turn yet never show up as a tool call. Each is fingerprinted
# so two machines can be compared at a glance.

def short_hash(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:7]
    except Exception:
        return None


def instruction_kind(path):
    if path.endswith(os.path.join("memory", "MEMORY.md")):
        return "memory"
    name = os.path.basename(path)
    if name.startswith("settings") and name.endswith(".json"):
        return "settings"
    return "claude_md"


def memory_entries(path):
    try:
        with open(path) as fh:
            return sum(1 for line in fh if line.lstrip().startswith("- "))
    except Exception:
        return 0


def instruction_entry(path, scope=None, reason=None):
    entry = {
        "kind": instruction_kind(path),
        "scope": scope,
        "path": path,
        "hash": short_hash(path),
        "reason": reason,
    }
    if entry["kind"] == "memory":
        entry["count"] = memory_entries(path)
    return entry


def scan_instructions(cwd, transcript_path):
    """What Claude Code would load for a session rooted at cwd."""
    found = []
    seen = set()

    def add(path, scope, reason="scan"):
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        found.append(instruction_entry(path, scope, reason))

    home = os.path.expanduser("~")
    add(os.path.join(home, ".claude", "CLAUDE.md"), "User")

    if cwd:
        # Ancestors are loaded too; list them outermost first.
        chain = []
        d = os.path.abspath(cwd)
        while True:
            chain.append(d)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        for d in reversed(chain):
            add(os.path.join(d, "CLAUDE.md"), "Project")
            add(os.path.join(d, ".claude", "CLAUDE.md"), "Project")
            add(os.path.join(d, "CLAUDE.local.md"), "Local")

    # Auto-memory lives beside the transcript, keyed by the launch directory.
    if transcript_path:
        add(os.path.join(os.path.dirname(transcript_path), "memory", "MEMORY.md"), "AutoMem")

    add(os.path.join(home, ".claude", "settings.json"), "User")
    if cwd:
        add(os.path.join(cwd, ".claude", "settings.json"), "Project")
        add(os.path.join(cwd, ".claude", "settings.local.json"), "Local")
    return found


def hook_total(instructions):
    total = 0
    for entry in instructions:
        if entry.get("kind") != "settings":
            continue
        try:
            with open(entry["path"]) as fh:
                hooks = (json.load(fh) or {}).get("hooks") or {}
            total += sum(len(g.get("hooks") or []) for groups in hooks.values() for g in groups)
        except Exception:
            pass
    return total


def refresh_instructions(previous, cwd, transcript_path):
    """Rescan, keeping files that only an InstructionsLoaded event revealed
    (nested CLAUDE.md files the scan cannot know about)."""
    current = scan_instructions(cwd, transcript_path)
    paths = {e["path"] for e in current}
    for old in previous or []:
        path = old.get("path")
        if path and path not in paths and os.path.isfile(path):
            current.append(instruction_entry(path, old.get("scope"), old.get("reason")))
    return current


def find_row(calls, call):
    for existing in calls:
        if (existing.get("kind") == call["kind"]
                and existing.get("server") == call["server"]
                and existing.get("name") == call["name"]):
            return existing
    return None


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


def append_line(log, record):
    try:
        if os.path.exists(log) and os.path.getsize(log) > MAX_HISTORY_BYTES:
            os.replace(log, log + ".1")
        with open(log, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def append_history(record):
    append_line(HISTORY, record)


def log_instruction_changes(entries, session_id):
    """Log only what differs from the last fingerprint seen for each file.

    The baseline is global rather than per session: a new session is not a
    change, and auto-memory in particular is rewritten by the model itself,
    so what matters is when a file's content moved, whichever session saw it.
    """
    seen = load_json(FINGERPRINTS)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    dirty = False

    for entry in entries:
        path, digest = entry.get("path"), entry.get("hash")
        if not path or not digest:
            continue
        before = seen.get(path)
        if before is None:
            event = "first_seen"
        elif before.get("hash") != digest:
            event = "changed"
        else:
            continue
        record = {
            "ts": stamp,
            "event": event,
            "session": session_id,
            "kind": entry.get("kind"),
            "scope": entry.get("scope"),
            "path": path,
            "hash": digest,
            "prev_hash": (before or {}).get("hash"),
        }
        if entry.get("kind") == "memory":
            record["count"] = entry.get("count")
            record["prev_count"] = (before or {}).get("count")
        append_line(INSTRUCTION_LOG, record)
        seen[path] = {"hash": digest, "count": entry.get("count")}
        dirty = True

    # Only a file that is gone from disk counts as removed; one that merely
    # belongs to a different project than this session's does not.
    for path in list(seen):
        if not os.path.exists(path):
            append_line(INSTRUCTION_LOG, {
                "ts": stamp, "event": "removed", "session": session_id,
                "path": path, "prev_hash": seen[path].get("hash"),
            })
            del seen[path]
            dirty = True

    if dirty:
        save_json(FINGERPRINTS, seen)


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
                raw = event.get("prompt") or ""
                cwd = event.get("cwd")
                command = slash_command(raw)
                invoked = command if command and is_known_skill(command, cwd) else None

                # A <command-name> envelope is normally harness noise, but when
                # it names a real skill the user did start a turn with it.
                if invoked is None and is_injected(raw):
                    return

                instructions = refresh_instructions(
                    state.get("instructions"), cwd, event.get("transcript_path"))
                state = {
                    "session": session_id,
                    "cwd": cwd,
                    "prompt": display_prompt(raw),
                    "status": "running",
                    "started": now(),
                    "updated": now(),
                    "calls": [],
                    "instructions": instructions,
                    "hooks": hook_total(instructions),
                }
                log_instruction_changes(instructions, session_id)
                if invoked:
                    # Recorded here because a slash-invoked skill may be expanded
                    # by the harness and never reach PreToolUse at all.
                    state["prompt"] = state["prompt"] or "/" + invoked
                    state["calls"] = [{
                        "kind": "skill",
                        "server": None,
                        "name": invoked,
                        "count": 1,
                        "time": now(),
                        "origin": "user",
                        "from_prompt": True,
                    }]
                save_json(path, state)
                publish(state)
                prune_sessions()
                return

            if mode == "instr":
                file_path = event.get("file_path")
                if not file_path:
                    return
                if not state:
                    state = {
                        "session": session_id,
                        "cwd": event.get("cwd"),
                        "prompt": None,
                        "status": "running",
                        "started": now(),
                        "calls": [],
                    }
                entry = instruction_entry(
                    file_path, event.get("memory_type"), event.get("load_reason"))
                entries = [e for e in (state.get("instructions") or [])
                           if e.get("path") != file_path]
                entries.append(entry)
                log_instruction_changes([entry], session_id)
                state["instructions"] = entries
                state["hooks"] = hook_total(entries)
                state["updated"] = now()
                save_json(path, state)
                publish(state)
                return

            if mode == "done":
                call = classify(event)
                elapsed = event.get("duration_ms")
                if call is None or not isinstance(elapsed, (int, float)) or not state:
                    return
                row = find_row(state.get("calls") or [], call)
                if row is None:
                    return
                row["ms"] = int(elapsed)
                state["updated"] = now()
                save_json(path, state)
                publish(state)
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
            row = find_row(calls, call)
            if row is None:
                call["count"] = 1
                call["time"] = now()
                call["origin"] = "auto"
                calls.append(call)
            elif row.get("from_prompt") and not row.get("claimed"):
                # The Skill call the user's /command produced — already counted.
                row["claimed"] = True
                row["time"] = now()
            else:
                row["count"] = int(row.get("count", 1)) + 1
                row["time"] = now()

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
