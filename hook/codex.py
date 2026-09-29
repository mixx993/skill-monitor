#!/usr/bin/env python3
"""Read-only Codex hook adapter. Python 3.11+, macOS/Linux, no dependencies.

Contract: https://learn.chatgpt.com/docs/hooks (2026-09-29).
Skill rows describe explicit requests or file-read attempts, NOT execution success.
No transcript scraping, command execution, network access or model instructions.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time
import tomllib

MAX_CALLS = 60
MAX_RECORDS = 400
MAX_INPUT = 4 * 1024 * 1024
EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "Stop", "Interrupt", "SessionEnd"}


def load(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append(path, value):
    if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
        os.replace(path, path.with_suffix(path.suffix + ".1"))
    with path.open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def key(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def project_chain(cwd):
    """Root -> cwd; a .git file also marks a worktree. No git subprocess."""
    current = Path(cwd).absolute()
    chain = [current]
    for directory in [current, *current.parents]:
        if (directory / ".git").exists():
            return list(reversed(chain))
        parent = directory.parent
        if parent != directory:
            chain.append(parent)
    return [current]


def instructions(home, cwd):
    """Candidates only: effective config/trust/truncation aren't observable here."""
    chain = project_chain(cwd)
    found = []
    fallbacks = []
    configs = [(home / "config.toml", "User")]
    configs += [(p / ".codex/config.toml", "Project") for p in chain]
    for path, scope in configs:
        if not path.is_file():
            continue
        try:
            config = tomllib.loads(path.read_text())
            names = config.get("project_doc_fallback_filenames")
            if isinstance(names, list):
                fallbacks = [n for n in names if isinstance(n, str)
                             and Path(n).name == n and n not in (".", "..")]
        except (OSError, ValueError):
            pass
        found.append((path, "settings", scope))
    for directory, scope in [(home, "User")] + [(p, "Project") for p in chain]:
        names = ["AGENTS.override.md", "AGENTS.md"]
        if scope == "Project":
            names += fallbacks
        for name in names:
            path = directory / name
            if path.is_file() and path.stat().st_size:
                found.append((path, "agents_md", scope))
                break
    for directory, scope in [(home, "User")] + [(p / ".codex", "Project") for p in chain]:
        if (directory / "hooks.json").is_file():
            found.append((directory / "hooks.json", "settings", scope))
    result = {}
    for path, kind, scope in found:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:7]
            result[str(path)] = dict(path=str(path), kind=kind, scope=scope,
                                     hash=digest, reason="candidate")
        except OSError:
            continue
    return list(result.values())


def skill_name(path):
    """Read only a small YAML header; no YAML constructors or command expansion."""
    try:
        with path.open() as stream:
            header = stream.read(8192)
        if header.startswith("---\n"):
            header = header.split("\n---", 1)[0]
            match = re.search(r"^name:[ \t]*[\"']?([A-Za-z0-9_.:-]+)[\"']?[ \t]*$", header, re.M)
            if match:
                return match.group(1)
    except (OSError, UnicodeError):
        pass
    return path.parent.name


def skill_catalog(home, cwd):
    roots = [Path.home() / ".agents/skills", home / "skills", Path("/etc/codex/skills")]
    roots += [p / ".agents/skills" for p in project_chain(cwd)]
    result = {}
    # Bounded discovery; plugin caches/disabled plugins are deliberately not guessed.
    for root in roots:
        for pattern in ("*/SKILL.md", ".system/*/SKILL.md"):
            for path in root.glob(pattern):
                if path.is_file():
                    paths = result.setdefault(skill_name(path), [])
                    resolved = str(path.resolve())
                    if resolved not in paths:
                        paths.append(resolved)
                if sum(map(len, result.values())) >= 1000:
                    return result
    return result


def read_paths(command, cwd, depth=0):
    """Recognize simple literal reads, never execute or expand a shell command.

    Compound commands, variables, substitution and redirection intentionally fall
    outside the v1 recognizer. A missed read is preferable to a fabricated one.
    """
    if not isinstance(command, str) or depth > 2:
        return []
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    if len(tokens) == 3 and Path(tokens[0]).name in ("bash", "sh", "zsh") and tokens[1] in ("-c", "-lc"):
        return read_paths(tokens[2], cwd, depth + 1)
    if any(char in command for char in ("$", "`", "|", ";", "&", ">", "<", "\n")):
        return []
    if not tokens:
        return []
    tool = Path(tokens[0]).name
    if tool == "cat":
        operands = tokens[1:]
        if any(t.startswith("-") and t != "--" for t in operands):
            return []
    elif tool in ("head", "tail"):
        operands = tokens[1:]
        if len(operands) >= 2 and operands[0] == "-n" and operands[1].isdigit():
            operands = operands[2:]
        if any(t.startswith("-") and t != "--" for t in operands):
            return []
    elif tool == "sed" and len(tokens) >= 4 and tokens[1] == "-n" and re.fullmatch(r"\d+(?:,\d+)?p", tokens[2]):
        operands = tokens[3:]
    else:
        return []
    paths = []
    for token in operands:
        if token == "--":
            continue
        path = Path(token).expanduser()
        if not path.is_absolute():
            path = Path(cwd) / path
        if path.name == "SKILL.md" and path.is_file():
            path = path.resolve()
            if path not in paths:
                paths.append(path)
    return paths


def classify(event):
    tool = event.get("tool_name", "")
    args = event.get("tool_input")
    if not isinstance(args, dict):
        return []
    if isinstance(tool, str) and tool.startswith("mcp__"):
        parts = tool.split("__", 2)
        if len(parts) == 3:
            return [dict(kind="mcp", server=parts[1], name=parts[2], evidence="tool_hook")]
    if tool in ("Bash", "exec_command", "shell_command"):
        cwd = args.get("workdir") or event.get("cwd") or os.getcwd()
        return [dict(kind="skill", server=None, name=skill_name(p), path=str(p),
                     evidence="read_attempt", origin="unknown")
                for p in read_paths(args.get("command", args.get("cmd")), cwd)]
    return []


def row_key(call):
    return (call["kind"], call.get("server"), call["name"], call.get("path"))


def add_call(state, call, stamp):
    calls = state["calls"]
    row = next((r for r in calls if row_key(r) == row_key(call)), None)
    if row is None:
        row = dict(call, count=1, time=stamp)
        calls.append(row)
    elif row.get("evidence") == "explicit_request" and call.get("evidence") == "read_attempt":
        row.update(evidence="read_attempt", time=stamp)
    else:
        row.update(count=row["count"] + 1, time=stamp)
    state["calls"] = calls[-MAX_CALLS:]
    return row


def fingerprint_changes(base, entries, session):
    previous = load(base / "fingerprints.json")
    for entry in entries:
        path, digest = entry["path"], entry["hash"]
        if previous.get(path) != digest:
            append(base / "instructions.jsonl", dict(ts=time.time(), session=session,
                path=path, hash=digest, prev_hash=previous.get(path),
                event="changed" if path in previous else "first_seen", evidence="candidate"))
            previous[path] = digest
    for path in list(previous):
        if not Path(path).exists():
            append(base / "instructions.jsonl", dict(ts=time.time(), session=session,
                path=path, prev_hash=previous.pop(path), event="removed"))
    save(base / "fingerprints.json", previous)


def process(event, home):
    kind = event.get("hook_event_name")
    session = event.get("session_id")
    if kind not in EVENTS or not isinstance(session, str) or not session:
        return
    if kind not in ("SessionStart", "SessionEnd") and not event.get("turn_id"):
        return  # supported Codex turn events have IDs; don't merge unidentified turns
    base = home / "skill-monitor"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (base / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active_path = base / "sessions" / (key(session) + ".json")
        active = load(active_path)
        turn = event.get("turn_id") or active.get("turn_id") or "session"
        if not isinstance(turn, str):
            return
        path = base / "turns" / (key(session, turn) + ".json")
        state = load(path)
        stamp = time.strftime("%H:%M:%S")
        if not state:
            state = dict(source="codex", session=session, turn_id=turn,
                         cwd=event.get("cwd") or os.getcwd(), prompt=None,
                         status="idle" if kind == "SessionStart" else "running",
                         started=stamp, calls=[], pending={}, completed=[])
        if kind in ("SessionStart", "UserPromptSubmit"):
            state["instructions"] = instructions(home, state["cwd"])
            fingerprint_changes(base, state["instructions"], session)
        if kind == "UserPromptSubmit":
            raw = event.get("prompt")
            if not isinstance(raw, str):
                return
            state.update(prompt=" ".join(raw.split())[:160], status="running")
            catalog = skill_catalog(home, state["cwd"])
            for name in set(re.findall(r"(?<![\w\\])\$([A-Za-z0-9_][A-Za-z0-9_.:-]*)", raw)):
                paths = catalog.get(name, [])
                if len(paths) != 1:  # duplicate names require an exact observed path
                    continue
                call = dict(kind="skill", server=None, name=name, path=str(Path(paths[0]).resolve()),
                            origin="user", evidence="explicit_request")
                if not any(row_key(r) == row_key(call) for r in state["calls"]):
                    add_call(state, call, stamp)
                    append(base / "history.jsonl", dict(call, ts=time.time(), session=session, turn_id=turn))
        elif kind in ("PreToolUse", "PostToolUse"):
            call_id = event.get("tool_use_id")
            if not isinstance(call_id, str) or not call_id:
                return  # don't invent IDs or incorrectly pair concurrent calls
            if kind == "PreToolUse":
                if call_id in state["pending"] or call_id in state["completed"]:
                    return
                calls = classify(event)
                if not calls:
                    return
                for call in calls:
                    row = add_call(state, call, stamp)
                    if row.get("origin"):
                        call["origin"] = row["origin"]
                    append(base / "history.jsonl", dict(call, ts=time.time(), session=session,
                        turn_id=turn, call_id=call_id, phase="start"))
                state["pending"][call_id] = dict(start=time.monotonic_ns(), calls=calls)
                # Bound unmatched calls (approval denied / killed process / missing Post).
                state["pending"] = dict(list(state["pending"].items())[-MAX_RECORDS:])
                state["status"] = "running"
            else:
                pending = state["pending"].pop(call_id, None)
                if pending is None:
                    return
                ms = max(0, (time.monotonic_ns() - pending["start"]) // 1_000_000)
                for call in pending["calls"]:
                    row = next((r for r in state["calls"] if row_key(r) == row_key(call)), None)
                    if row:
                        row["ms"] = ms
                    append(base / "history.jsonl", dict(call, ts=time.time(), session=session,
                        turn_id=turn, call_id=call_id, phase="end", ms=ms))
                state["completed"] = (state["completed"] + [call_id])[-MAX_RECORDS:]
        elif kind in ("Stop", "Interrupt", "SessionEnd"):
            state["status"] = "interrupted" if kind == "Interrupt" else "done"
        state["updated"] = stamp
        state["updated_at"] = time.time()
        save(path, state)
        # Late events update their own ledger without taking over a newer turn.
        if not active or active.get("turn_id") == turn or kind == "UserPromptSubmit":
            save(active_path, state)
            public = {k: v for k, v in state.items() if k not in ("pending", "completed")}
            save(base / "state.json", public)
        if kind == "UserPromptSubmit":
            for folder, maximum in (("turns", 200), ("sessions", 40)):
                files = sorted((base / folder).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[maximum:]:
                    old.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--monitor-id", choices=["skillmonitor-codex-v1"])
    args = parser.parse_args()
    home = (args.codex_home or Path(os.environ.get("CODEX_HOME", "~/.codex"))).expanduser().absolute()
    os.umask(0o077)
    try:
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("event exceeds input limit")
        event = json.loads(raw)
        if isinstance(event, dict):
            process(event, home)
    except Exception as exc:
        # Report only exception type, never raw prompts, credentials or tool payloads.
        print("SkillMonitor: ignored event (" + type(exc).__name__ + ")", file=sys.stderr)
    # In particular, Stop expects JSON. Never inject context or block a turn.
    print("{}")


if __name__ == "__main__":
    main()
