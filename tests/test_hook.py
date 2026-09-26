#!/usr/bin/env python3
"""Behavioural tests for hook/log.py.

Each case runs the hook as a subprocess against a throwaway HOME, so nothing
here can touch a real ~/.claude.

    python3 tests/test_hook.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "hook", "log.py")

failures = []
home = None


def fire(mode, payload):
    subprocess.run(
        [sys.executable, LOG, mode],
        input=json.dumps(payload).encode(),
        env=dict(os.environ, HOME=home),
        check=True,
    )


def state():
    path = os.path.join(home, ".claude", "skill-monitor", "state.json")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def rows():
    return [(c["name"], c.get("origin"), c["count"], c.get("ms"))
            for c in state().get("calls", [])]


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        failures.append(label)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def make_skill(name):
    d = os.path.join(home, ".claude", "skills", name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "SKILL.md"), "w").write("# %s\n" % name)


def main():
    global home
    home = tempfile.mkdtemp(prefix="skillmon-test-")
    try:
        make_skill("demo-skill")

        print("a prompt starts a task")
        fire("prompt", {"session_id": "A", "cwd": "/w", "prompt": "hello"})
        check("prompt recorded", state().get("prompt"), "hello")
        check("no calls yet", rows(), [])

        print("tool calls accumulate and dedupe")
        fire("tool", {"session_id": "A", "tool_name": "Skill",
                      "tool_input": {"skill": "auto-one"}})
        fire("tool", {"session_id": "A", "tool_name": "Skill",
                      "tool_input": {"skill": "auto-one"}})
        fire("tool", {"session_id": "A", "tool_name": "mcp__srv__do_thing"})
        check("deduped with origin", rows(),
              [("auto-one", "auto", 2, None), ("do_thing", "auto", 1, None)])

        print("non-matching tools are ignored")
        fire("tool", {"session_id": "A", "tool_name": "Bash",
                      "tool_input": {"command": "ls"}})
        check("bash not recorded", len(rows()), 2)

        print("PostToolUse attaches the duration")
        fire("done", {"session_id": "A", "tool_name": "mcp__srv__do_thing",
                      "duration_ms": 1234})
        check("duration attached", rows()[1], ("do_thing", "auto", 1, 1234))

        print("harness-injected prompts do not reset the task")
        fire("prompt", {"session_id": "A", "cwd": "/w",
                        "prompt": "<task-notification><task-id>x</task-id>"})
        check("task survives notification", state().get("prompt"), "hello")
        fire("prompt", {"session_id": "A", "cwd": "/w",
                        "prompt": "[SYSTEM NOTIFICATION - NOT USER INPUT] done"})
        check("task survives system banner", state().get("prompt"), "hello")
        fire("prompt", {"session_id": "A", "cwd": "/w",
                        "prompt": "<command-name>/model</command-name>"})
        check("task survives a built-in command", state().get("prompt"), "hello")

        print("a slash-invoked skill is a task, and is attributed to the user")
        fire("prompt", {"session_id": "A", "cwd": "/w",
                        "prompt": "/demo-skill go"})
        check("slash command starts a task", state().get("prompt"), "/demo-skill go")
        check("recorded as user origin", rows(), [("demo-skill", "user", 1, None)])
        fire("tool", {"session_id": "A", "tool_name": "Skill",
                      "tool_input": {"skill": "demo-skill"}})
        check("the model's matching call is claimed, not counted",
              rows(), [("demo-skill", "user", 1, None)])
        fire("tool", {"session_id": "A", "tool_name": "Skill",
                      "tool_input": {"skill": "demo-skill"}})
        check("a further call does count", rows(), [("demo-skill", "user", 2, None)])

        print("an unknown slash command is just a prompt")
        fire("prompt", {"session_id": "A", "cwd": "/w", "prompt": "/not-a-skill hi"})
        check("no skill row", rows(), [])

        print("sessions do not contaminate each other")
        fire("prompt", {"session_id": "A", "cwd": "/w", "prompt": "task A"})
        fire("prompt", {"session_id": "B", "cwd": "/w", "prompt": "task B"})
        fire("tool", {"session_id": "A", "tool_name": "Skill",
                      "tool_input": {"skill": "only-in-a"}})
        check("A's call lands under A's prompt",
              (state().get("prompt"), rows()), ("task A", [("only-in-a", "auto", 1, None)]))
        fire("tool", {"session_id": "B", "tool_name": "Skill",
                      "tool_input": {"skill": "only-in-b"}})
        check("B's call lands under B's prompt",
              (state().get("prompt"), rows()), ("task B", [("only-in-b", "auto", 1, None)]))

        print("stop marks the task finished")
        fire("stop", {"session_id": "B"})
        check("status done", state().get("status"), "done")

        print("instruction files are found, fingerprinted and survive turns")
        claude_dir = os.path.join(home, ".claude")
        write(os.path.join(claude_dir, "CLAUDE.md"), "global rules")
        project = os.path.join(home, "work", "proj")
        write(os.path.join(project, "CLAUDE.md"), "project rules")
        transcript_dir = os.path.join(claude_dir, "projects", "-work-proj")
        write(os.path.join(transcript_dir, "memory", "MEMORY.md"),
              "- [a](a.md) — one\n- [b](b.md) — two\n- [c](c.md) — three\n")
        write(os.path.join(claude_dir, "settings.json"), json.dumps({"hooks": {
            "PreToolUse": [{"hooks": [{"type": "command", "command": "x"},
                                      {"type": "command", "command": "y"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "z"}]}],
        }}))
        transcript = os.path.join(transcript_dir, "C.jsonl")

        fire("prompt", {"session_id": "C", "cwd": project,
                        "transcript_path": transcript, "prompt": "go"})
        kinds = sorted((e["kind"], e["scope"]) for e in state()["instructions"])
        check("global, project, memory and settings found", kinds,
              [("claude_md", "Project"), ("claude_md", "User"),
               ("memory", "AutoMem"), ("settings", "User")])
        memory = [e for e in state()["instructions"] if e["kind"] == "memory"][0]
        check("memory entries counted", memory["count"], 3)
        check("hooks counted across settings", state()["hooks"], 3)

        before = {e["path"]: e["hash"] for e in state()["instructions"]}
        write(os.path.join(project, "CLAUDE.md"), "project rules, edited")
        fire("prompt", {"session_id": "C", "cwd": project,
                        "transcript_path": transcript, "prompt": "again"})
        after = {e["path"]: e["hash"] for e in state()["instructions"]}
        changed = sorted(os.path.basename(os.path.dirname(p))
                         for p in after if after[p] != before.get(p))
        check("an edit changes exactly that file's fingerprint", changed, ["proj"])

        print("a CLAUDE.md loaded mid-session is kept, and outlives the turn")
        nested = os.path.join(project, "sub", "CLAUDE.md")
        write(nested, "nested rules")
        fire("instr", {"session_id": "C", "cwd": project, "file_path": nested,
                       "memory_type": "Project", "load_reason": "nested_traversal",
                       "transcript_path": transcript})
        reasons = {e["path"]: e["reason"] for e in state()["instructions"]}
        check("nested file recorded with its load reason",
              reasons.get(nested), "nested_traversal")
        fire("prompt", {"session_id": "C", "cwd": project,
                        "transcript_path": transcript, "prompt": "next turn"})
        check("still listed after the next prompt",
              nested in {e["path"] for e in state()["instructions"]}, True)
        check("calls were reset but instructions were not",
              (rows(), len(state()["instructions"])), ([], 5))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print()
    if failures:
        print("%d test(s) failed" % len(failures))
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
