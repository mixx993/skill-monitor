#!/bin/bash
# Build SkillMonitor.app (a menu-bar-less floating panel) into ./dist
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/dist/SkillMonitor.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

BIN="$APP/Contents/MacOS/SkillMonitor"
SOURCES=("$ROOT/app/Island.swift" "$ROOT/app/main.swift")

compile() { # <target> <output>
  swiftc -O -target "$1" -framework AppKit -framework SwiftUI -o "$2" "${SOURCES[@]}"
}

if [ "${UNIVERSAL:-0}" = "1" ]; then
  # Release builds run on one runner but have to start on both architectures.
  TMP="$(mktemp -d)"
  compile arm64-apple-macos13.0  "$TMP/arm64"
  compile x86_64-apple-macos13.0 "$TMP/x86_64"
  lipo -create "$TMP/arm64" "$TMP/x86_64" -output "$BIN"
  rm -rf "$TMP"
else
  case "$(uname -m)" in
    arm64) compile arm64-apple-macos13.0  "$BIN" ;;
    *)     compile x86_64-apple-macos13.0 "$BIN" ;;
  esac
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SkillMonitor</string>
  <key>CFBundleDisplayName</key><string>SkillMonitor</string>
  <key>CFBundleIdentifier</key><string>local.mixx.skillmonitor</string>
  <key>CFBundleExecutable</key><string>SkillMonitor</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || true
echo "built: $APP"
