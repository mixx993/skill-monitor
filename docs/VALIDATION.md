# Codex preview validation — 2026-09-29

Base: `a826443504df40703572c8112ab8fbab3d8a3f38` (upstream main).
Local development branch: `feat/codex-monitor`.

Completed in the Linux development environment:

- Existing `python3 tests/test_hook.py`: all assertions passed.
- `python3 -m unittest discover -s tests -p 'test_codex*.py' -v`: 20 tests passed.
- Python syntax compilation for adapter, installer and tests: passed.
- `bash -n` on the five build/install/uninstall scripts: passed.
- `git diff --check`: passed.

The Codex tests send documented JSON hook fixtures through real Python
subprocesses against temporary home directories. They exercise persistence,
concurrent calls, call-ID pairing, delayed events, read detection, instruction
candidates, installation preservation and malformed input. They do not run a
real Codex model session.

Pending live acceptance:
- Real Codex CLI `/hooks` trust and end-to-end event delivery on a Mac.
- Frontmost-app visibility, hover interaction and simultaneous Claude sessions.

Publication was explicitly authorized after the initial local development pass.
The branch is published in [draft PR #1](https://github.com/mixx993/skill-monitor/pull/1).
Its GitHub Actions checks track macOS Swift compilation, universal bundle
validation and offscreen UI rendering. Consult the latest PR check for the
current build result; these are separate from real Codex session acceptance.

The first Mac CI run found a test-path expectation that did not account for
macOS's `/var` -> `/private/var` symlink. The expectation now compares canonical
paths, matching the adapter's existing path normalization.

See `CODEX.md` for installation and the live acceptance checklist. This is a
source preview, not a verified macOS release binary.
