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
