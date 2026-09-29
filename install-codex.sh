#!/bin/bash
# Build the shared island and install the Codex adapter. Never bypass hook trust.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY=""
for candidate in python3 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        PY="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "error: Codex adapter requires Python 3.11 or newer" >&2
    exit 1
fi
if [ "${1:-}" = "--hooks-only" ]; then
    shift
    exec "$PY" "$ROOT/tools/codex_setup.py" "$@"
fi
if [ "$(uname -s)" != "Darwin" ]; then
    echo "error: the island requires macOS; use --hooks-only for the adapter" >&2
    exit 1
fi
if ! command -v codex >/dev/null 2>&1; then
    echo "error: install Codex CLI first; this version targets local CLI sessions" >&2
    exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
    echo "error: install Xcode Command Line Tools (xcode-select --install)" >&2
    exit 1
fi
# Compile this checkout. Old release binaries do not contain Codex UI support.
"$ROOT/build.sh"
"$PY" "$ROOT/tools/codex_setup.py" "$@"
pkill -x SkillMonitor 2>/dev/null || true
open "$ROOT/dist/SkillMonitor.app"
echo "Display configuration: ~/.config/skill-monitor/config.json"
