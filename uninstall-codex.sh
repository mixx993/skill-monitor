#!/bin/bash
# Keep the shared island and Claude integration running.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/install-codex.sh" --hooks-only --uninstall "$@"
