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

Pending:

- macOS Swift compilation and universal bundle validation.
- Offscreen UI rendering, including the new Codex fixture.
- Real Codex CLI `/hooks` trust and end-to-end event delivery on a Mac.
- Frontmost-app visibility, hover interaction and simultaneous Claude sessions.

The updated GitHub Actions workflow defines the Mac build and preview steps.
They have NOT run for this change: public-repository upload was blocked by
automatic approval review pending explicit authorization to publish. No
remote branch, PR or release was created during this development pass.

See `CODEX.md` for installation and the live acceptance checklist. This is a
source preview, not a verified macOS release binary.
