#!/bin/sh
# Archive SaldoVibe and upload it to TestFlight.
#
# Needs: Xcode signed in (Xcode → Settings → Accounts) with an Apple ID that belongs to team
# Y4XHJ3DYSS, and an app record "SaldoVibe" with bundle id se.saldovibe.app in App Store Connect.
# Automatic signing creates the distribution certificate and profile on first run.
#
# The build number is the current minute (yyyymmddHHMM) so every upload is unique without
# touching the project file; the marketing version stays MARKETING_VERSION in project.pbxproj.
set -eu
cd "$(dirname "$0")/.."
BUILD=${BUILD_NUMBER:-$(date +%Y%m%d%H%M)}
ARCHIVE=${ARCHIVE_PATH:-build/SaldoVibe.xcarchive}

xcodebuild archive \
  -project SaldoVibe.xcodeproj -scheme SaldoVibe -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$ARCHIVE" \
  -allowProvisioningUpdates CURRENT_PROJECT_VERSION="$BUILD" | tail -3

xcodebuild -exportArchive \
  -archivePath "$ARCHIVE" -exportOptionsPlist ExportOptions.plist -exportPath build/export \
  -allowProvisioningUpdates | tail -5

echo "Build $BUILD laddades upp. Den dyker upp under TestFlight i App Store Connect efter bearbetning (några minuter)."
