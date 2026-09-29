"""Documented hook-contract fixtures; not a substitute for live Codex/macOS QA."""
import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "hook/codex.py"
SETUP = ROOT / "tools/codex_setup.py"


class CodexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.codex = self.home / "custom codex"
        self.repo = self.home / "project"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.codex))
        self.base = self.codex / "skill-monitor"

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def skill(self, name="demo", root=None):
        return self.write((root or self.home / ".agents/skills") / name / "SKILL.md",
                          "---\nname: " + name + "\ndescription: fixture\n---\n# Instructions\n")

    def emit(self, kind, session="s1", turn="t1", **fields):
        event = dict(hook_event_name=kind, session_id=session, cwd=str(self.repo))
        event.update(fields)
        if turn is not None:
            event["turn_id"] = turn
        result = subprocess.run([sys.executable, str(ADAPTER)], input=json.dumps(event),
                                text=True, capture_output=True, env=self.env, check=True)
        self.assertEqual(result.stdout.strip(), "{}")
        self.assertEqual(result.stderr, "")
        return event

    def state(self):
        return json.loads((self.base / "state.json").read_text())

    def tool(self, call="c1", kind="PreToolUse", **fields):
        defaults = dict(tool_use_id=call, tool_name="mcp__docs__read", tool_input={})
        defaults.update(fields)
        return self.emit(kind, **defaults)

    def setup_run(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SETUP), *args], text=True,
                                capture_output=True, env=self.env)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result

    def test_task_lifecycle_and_no_payload_persistence(self):
        self.emit("SessionStart", turn=None)
        self.assertEqual(self.state()["status"], "idle")
        self.emit("UserPromptSubmit", prompt="make a diagram")
        self.tool(tool_input={"api_key": "TOP-SECRET"})
        self.tool(kind="PostToolUse", tool_response={"secret": "TOP-SECRET"})
        self.emit("Stop")
        state = self.state()
        self.assertEqual(state["source"], "codex")
        self.assertEqual(state["status"], "done")
        self.assertGreaterEqual(state["calls"][0]["ms"], 0)
        self.assertNotIn("pending", state)
        for path in self.base.rglob("*.json*"):
            self.assertNotIn("TOP-SECRET", path.read_text())

    def test_explicit_request_is_not_execution_and_claim_dedupes(self):
        skill = self.skill()
        self.emit("UserPromptSubmit", prompt="$demo create a diagram")
        row = self.state()["calls"][0]
        self.assertEqual((row["origin"], row["evidence"]), ("user", "explicit_request"))
        self.tool(tool_name="Bash", tool_input={"command": "cat " + shlex.quote(str(skill))})
        row = self.state()["calls"][0]
        self.assertEqual((row["count"], row["origin"], row["evidence"]), (1, "user", "read_attempt"))
        self.tool("c2", tool_name="Bash", tool_input={"command": "cat " + shlex.quote(str(skill))})
        self.assertEqual(self.state()["calls"][0]["count"], 2)

    def test_unknown_origin_never_marked_auto(self):
        skill = self.skill()
        self.emit("UserPromptSubmit", prompt="Use the demo skill please")
        self.tool(tool_name="Bash", tool_input={"command": "sed -n '1,200p' " + shlex.quote(str(skill))})
        row = self.state()["calls"][0]
        self.assertEqual(row["origin"], "unknown")
        self.assertEqual(row["evidence"], "read_attempt")

    def test_unknown_dollar_and_ambiguous_skill_not_fabricated(self):
        self.skill()
        self.skill(root=self.repo / ".agents/skills")
        self.emit("UserPromptSubmit", prompt="$PATH $not-installed $demo")
        self.assertEqual(self.state()["calls"], [])

    def test_same_name_different_files_stay_distinct(self):
        a = self.skill()
        b = self.skill(root=self.repo / ".agents/skills")
        self.emit("UserPromptSubmit", prompt="inspect both")
        self.tool(tool_name="Bash", tool_input={"command": shlex.join(["cat", str(a), str(b)])})
        self.assertEqual(len(self.state()["calls"]), 2)

    def test_read_detection_does_not_execute_or_guess(self):
        skill = self.skill()
        self.emit("UserPromptSubmit", prompt="review")
        commands = ["echo " + str(skill), "test -f " + str(skill),
                    "cat " + str(skill) + " && touch " + str(self.home / "BAD"),
                    "cat $(touch " + str(self.home / "BAD") + ")", "cat $FILE", "cat missing/SKILL.md"]
        for i, command in enumerate(commands):
            self.tool(str(i), tool_name="Bash", tool_input={"command": command})
        self.assertEqual(self.state()["calls"], [])
        self.assertFalse((self.home / "BAD").exists())

    def test_shell_wrapper_relative_path_and_spaces(self):
        skill = self.skill(root=self.repo / "skills with spaces")
        command = shlex.join(["bash", "-lc", "cat " + shlex.quote(str(skill.relative_to(self.repo)))])
        self.tool(tool_name="Bash", tool_input={"command": command})
        self.assertEqual(self.state()["calls"][0]["path"], str(skill))

    def test_duplicate_events_and_steering_keep_counts(self):
        self.emit("UserPromptSubmit", prompt="one")
        self.tool()
        self.tool()
        self.emit("UserPromptSubmit", prompt="one, with changes")
        self.assertEqual(self.state()["calls"][0]["count"], 1)
        self.tool(kind="PostToolUse")
        self.tool(kind="PostToolUse")
        self.tool()
        self.assertEqual(self.state()["calls"][0]["count"], 1)

    def test_out_of_order_completions_pair_by_id(self):
        self.tool("a")
        self.tool("b", tool_name="mcp__docs__search")
        self.tool("b", kind="PostToolUse", tool_name="mcp__docs__search")
        rows = self.state()["calls"]
        self.assertNotIn("ms", rows[0])
        self.assertIn("ms", rows[1])
        self.tool("a", kind="PostToolUse")
        self.assertTrue(all("ms" in row for row in self.state()["calls"]))

    def test_late_completion_and_stop_cannot_replace_new_turn(self):
        self.emit("UserPromptSubmit", prompt="first")
        self.tool()
        self.emit("UserPromptSubmit", turn="t2", prompt="second")
        self.tool(kind="PostToolUse", turn="t1")
        self.emit("Stop", turn="t1")
        self.assertEqual((self.state()["turn_id"], self.state()["status"]), ("t2", "running"))
        self.assertEqual(self.state()["calls"], [])

    def test_completion_after_interrupt_does_not_restart_task(self):
        self.tool()
        self.emit("Interrupt")
        self.tool(kind="PostToolUse")
        self.assertEqual(self.state()["status"], "interrupted")

    def test_sessions_and_sanitization_collisions_are_isolated(self):
        self.emit("UserPromptSubmit", session="a/b", prompt="A")
        self.tool(session="a/b")
        self.emit("UserPromptSubmit", session="a_b", prompt="B")
        self.assertEqual(len(list((self.base / "sessions").glob("*.json"))), 2)
        self.assertEqual(self.state()["calls"], [])

    def test_concurrent_calls_do_not_lose_updates(self):
        self.emit("UserPromptSubmit", prompt="parallel tools")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.tool(str(i)), range(16)))
        self.assertEqual(self.state()["calls"][0]["count"], 16)

    def test_instruction_precedence_worktree_and_candidate_evidence(self):
        global_file = self.write(self.codex / "AGENTS.md", "global")
        self.write(self.repo / "AGENTS.md", "ignored")
        override = self.write(self.repo / "AGENTS.override.md", "selected")
        nested = self.repo / "src"
        nested.mkdir()
        fallback = self.write(nested / "TEAM.md", "team")
        self.write(self.codex / "config.toml", 'project_doc_fallback_filenames = ["TEAM.md"]')
        self.write(self.home / "AGENTS.md", "outside root")
        self.emit("UserPromptSubmit", cwd=str(nested), prompt="work")
        entries = self.state()["instructions"]
        docs = [e["path"] for e in entries if e["kind"] == "agents_md"]
        self.assertEqual(docs, list(map(str, [global_file, override, fallback])))
        self.assertTrue(all(e["reason"] == "candidate" for e in entries))

    def test_fingerprints_only_log_changes(self):
        path = self.write(self.codex / "AGENTS.md", "v1")
        self.emit("UserPromptSubmit", prompt="first")
        self.emit("UserPromptSubmit", turn="t2", prompt="second")
        path.write_text("v2")
        self.emit("UserPromptSubmit", turn="t3", prompt="third")
        events = [json.loads(line)["event"] for line in (self.base / "instructions.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["first_seen", "changed"])

    def test_malformed_input_fails_open(self):
        for raw in ("not json", "[]", '{"hook_event_name":"PreToolUse"}'):
            result = subprocess.run([sys.executable, str(ADAPTER)], input=raw, text=True,
                                    capture_output=True, env=self.env)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "{}")
        self.assertFalse((self.base / "state.json").exists())

    def test_install_idempotent_and_uninstall_preserves_other_handlers(self):
        hooks = {"description": "mine", "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "echo keep-me"}]}]}}
        path = self.write(self.codex / "hooks.json", json.dumps(hooks))
        self.setup_run()
        installed = path.read_bytes()
        self.setup_run()
        self.assertEqual(path.read_bytes(), installed)
        config = json.loads(path.read_text())
        self.assertEqual(len(config["hooks"]), 7)
        # Another handler added into our matcher group must also survive removal.
        config["hooks"]["Stop"][-1]["hooks"].append({"type": "command", "command": "echo also-keep"})
        path.write_text(json.dumps(config))
        self.setup_run("--uninstall")
        kept = json.loads(path.read_text())
        self.assertEqual(kept["description"], "mine")
        commands = [h["command"] for g in kept["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(commands, ["echo keep-me", "echo also-keep"])
        self.assertGreaterEqual(len(list(self.codex.glob("hooks.json.bak-skillmonitor-*"))), 1)

    def test_installed_command_handles_shell_metacharacters_in_path(self):
        self.codex = self.home / "codex ' $(do-not-run)"
        self.env["CODEX_HOME"] = str(self.codex)
        self.setup_run()
        config = json.loads((self.codex / "hooks.json").read_text())
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        event = dict(hook_event_name="UserPromptSubmit", session_id="s", turn_id="t", cwd=str(self.repo), prompt="test")
        result = subprocess.run(command, shell=True, input=json.dumps(event), capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.codex / "skill-monitor/state.json").is_file())

    def test_invalid_config_not_replaced(self):
        path = self.write(self.codex / "hooks.json", "broken{")
        self.setup_run(success=False)
        self.assertEqual(path.read_text(), "broken{")
        self.assertFalse((self.codex / "skill-monitor/bin/codex.py").exists())

    def test_legacy_display_preferences_and_claude_settings_preserved(self):
        settings = self.write(self.home / ".claude/settings.json", '{"keep":true}')
        self.write(self.home / ".claude/skill-monitor/config.json", '{"showWhenFrontmost":["my.terminal"]}')
        self.setup_run()
        ui = json.loads((self.home / ".config/skill-monitor/config.json").read_text())
        self.assertIn("my.terminal", ui["showWhenFrontmost"])
        self.assertIn(str(self.codex / "skill-monitor/state.json"), ui["stateFiles"])
        self.assertEqual(settings.read_text(), '{"keep":true}')
        output = self.setup_run("--status").stdout
        self.assertIn("Observed event: none", output)


if __name__ == "__main__":
    unittest.main()
