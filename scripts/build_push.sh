#!/bin/zsh
# 編譯 ulani_push.swift 並包成 ULANIPush.app（藍牙權限要靠 .app 才會被系統接受）
set -e
cd "$(dirname "$0")"
swiftc -O ulani_push.swift -o ulani_push_bin
APP=ULANIPush.app
rm -rf $APP; mkdir -p $APP/Contents/MacOS
mv ulani_push_bin $APP/Contents/MacOS/ULANIPush
cat > $APP/Contents/Info.plist <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.ulanipush.app</string>
<key>CFBundleName</key><string>ULANIPush</string>
<key>CFBundleExecutable</key><string>ULANIPush</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
<key>NSBluetoothAlwaysUsageDescription</key><string>與永恆曆溝通並更新照片</string>
</dict></plist>
PL
codesign --force --deep --sign - $APP
echo "built $APP"
