#!/bin/bash
# Install the macOS launchd agents that run the collector locally.
# Redundant with the GitHub Actions workflows — use either, or both.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR_DIR="$(cd "$HERE/.." && pwd)"
DATA_DIR="${BOW_DATA_DIR:-$HOME/Library/Application Support/BettingOnWar}"
mkdir -p "$DATA_DIR" "$HOME/Library/LaunchAgents"

for f in com.walberg.bow-book com.walberg.bow-full; do
  sed -e "s|__COLLECTOR_DIR__|$COLLECTOR_DIR|g" \
      -e "s|__DATA_DIR__|$DATA_DIR|g" \
      "$HERE/$f.plist" > "$HOME/Library/LaunchAgents/$f.plist"
  plutil -lint "$HOME/Library/LaunchAgents/$f.plist"
  launchctl unload "$HOME/Library/LaunchAgents/$f.plist" 2>/dev/null || true
  launchctl load  "$HOME/Library/LaunchAgents/$f.plist"
  echo "loaded $f"
done
echo "Uninstall: launchctl unload ~/Library/LaunchAgents/com.walberg.bow-{book,full}.plist"
