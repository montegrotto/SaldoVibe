#!/bin/sh
# Archive SaldoVibe and upload it to TestFlight.
#
# Needs: Xcode signed in (Xcode → Settings → Accounts) with an Apple ID that belongs to team
# 7MV2FUVY65, and an app record "SaldoVibe" with bundle id se.saldovibe.app in App Store Connect.
# Automatic signing creates the distribution certificate and profile on first run.
#
# The build number is the current minute (yyyymmddHHMM) so every upload is unique without
# touching the project file; the marketing version stays MARKETING_VERSION in project.pbxproj.
set -eu
cd "$(dirname "$0")/.."
BUILD=${BUILD_NUMBER:-$(date +%Y%m%d%H%M)}
ARCHIVE=${ARCHIVE_PATH:-build/SaldoVibe.xcarchive}
LOG=build/testflight.log
mkdir -p build

step() {
  # Run an xcodebuild step quietly; on failure show the errors, on success the last line.
  name=$1; shift
  if "$@" > "$LOG" 2>&1; then
    tail -1 "$LOG"
  else
    echo "$name misslyckades – ur $LOG:" >&2
    grep -E "error:|error |ERROR" "$LOG" | sort -u | head -20 >&2
    exit 1
  fi
}

step "Arkivering" xcodebuild archive \
  -project SaldoVibe.xcodeproj -scheme SaldoVibe -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$ARCHIVE" \
  -allowProvisioningUpdates CURRENT_PROJECT_VERSION="$BUILD"

step "Uppladdning" xcodebuild -exportArchive \
  -archivePath "$ARCHIVE" -exportOptionsPlist ExportOptions.plist -exportPath build/export \
  -allowProvisioningUpdates

echo "Build $BUILD laddades upp. Den dyker upp under TestFlight i App Store Connect efter bearbetning (några minuter)."
