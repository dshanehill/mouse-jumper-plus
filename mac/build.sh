#!/bin/bash
# Builds "Mouse Jumper+.app" and installs it in /Applications.
set -euo pipefail
cd "$(dirname "$0")"
APP="build/Mouse Jumper+.app"
rm -rf build && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -target arm64-apple-macos13.0 -framework Cocoa -framework ServiceManagement -framework SwiftUI -framework Vision Sources/main.swift -o "$APP/Contents/MacOS/MouseJumperPlus"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Mouse Jumper+</string>
  <key>CFBundleDisplayName</key><string>Mouse Jumper+</string>
  <key>CFBundleIdentifier</key><string>local.mousejumperplus</string>
  <key>CFBundleExecutable</key><string>MouseJumperPlus</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
[ -f icon/AppIcon.icns ] && cp icon/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
# SIGN_ID: your code-signing identity; "-" (the default) signs the app ad hoc for this Mac.
codesign --force --sign "${SIGN_ID:--}" --identifier local.mousejumperplus "$APP"
rm -rf "/Applications/Mouse Jumper+.app"
cp -R "$APP" /Applications/
echo "Installed /Applications/Mouse Jumper+.app"
