#!/usr/bin/env bash
# connect.sh builds idevice's tools and prints the no-app bring-up steps.
#
# This installs no automation runner on the phone. The only app involved is RC Pair, which
# makes the pairing record and hands it to your computer. It drives YOUR OWN device, on YOUR OWN
# Apple account, after YOU turn on Developer Mode yourself. Read ../ACCEPTABLE_USE.md first.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
ide="$here/third_party/idevice"

if [ ! -d "$ide" ]; then
  echo "Missing third_party/idevice. Run scripts/fetch-deps.sh first." >&2
  exit 1
fi

echo "Building idevice tools (the first build is slow)..."
cargo build --release --manifest-path "$ide/Cargo.toml" -p idevice-tools
bin="$ide/target/release"

echo
echo "Built tools in: $bin"
echo "Available tools:"
ls "$bin" 2>/dev/null | grep -vE '\.(d|rlib|so|dylib)$' | sed 's/^/  /' || true

cat <<'EOF'

The sequence. Each tool prints its exact options with -h, and flags vary by
version, so follow -h on the version you pulled. Nothing below is run for you.

  0) Hand-off    Install RC Pair on the device and turn on Developer Mode as it guides you
                 (the device restarts; a pairing made with it off does not verify). Then
                 pair in RC Pair and run
                   scripts/rcpair-receive.py
                 and scan the QR code it shows with the phone's Camera app (or copy its link
                 and tap Paste link in RC Pair). The phone asks you to confirm, then sends the
                 pairing record to this computer. The receiver saves <udid>.rppair.plist and
                 prints the device address. The record goes only to the computer you chose.
  1) Tunnel      open the CoreDevice tunnel with the record from step 0. The tools that read a
                 remote pairing file (such as "rppairing") take its path, so check -h. Pairing
                 over USB with the pairing tool works instead of step 0. The phone then shows
                 Trust and a 6-digit code, which is the consent step.
  2) Disk image  connect the device once to Xcode on a Mac. Xcode puts Apple's Developer
                 Disk Image on it. Redo it if it is gone, and after an iOS update.
  3) Drive       "hid"          tap / drag / type / buttons
                 "app_service"  launch / list apps
                 the device's developer screen stream to read the screen
  4) Decide      point your own agent or model at the tools above.
                 The decision layer (what to tap) is not in this repo. You bring it.

You provide: your device's address (on USB or the same LAN, mDNS finds it; across a
VPN use an explicit IP, mDNS does not cross a VPN), with the device connected once to Xcode
on a Mac so Apple's Developer Disk Image is on it.

Developer Mode must be on before you pair. You enable it with your passcode and the device reboots.
Some developer ports are refused on cellular.
EOF
