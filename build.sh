#!/bin/bash
# Build SkillMonitor.app (a menu-bar-less floating panel) into ./dist
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/dist/SkillMonitor.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

case "$(uname -m)" in
  arm64) TARGET="arm64-apple-macos13.0" ;;
  *)     TARGET="x86_64-apple-macos13.0" ;;
esac

swiftc -O \
  -target "$TARGET" \
  -framework AppKit -framework SwiftUI \
  -o "$APP/Contents/MacOS/SkillMonitor" \
  "$ROOT/app/Island.swift" "$ROOT/app/main.swift"

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
