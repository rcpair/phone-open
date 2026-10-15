#!/usr/bin/env bash
# connect.sh builds idevice's tools, fills your disk image folder if it has no valid disk image,
# and prints the no-app bring-up steps.
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

# Apple's disk image lives in one folder on this computer; everything after this reads only that
# folder. Fill it when it has no valid disk image: from Xcode if this is a Mac that has it,
# otherwise from the pinned public GitHub copy (every file SHA-256 checked against
# scripts/ddi-sha256.txt first).
# RCPAIR_DDI_FETCH=0 skips this and leaves the folder to you.
# shellcheck source=scripts/ddi-lib.sh
source "$here/scripts/ddi-lib.sh"
ddi_dir="$(ddi_target "")"
xcode_ddi="${RCPAIR_XCODE_DDI:-/Library/Developer/DeveloperDiskImages/iOS_DDI/Restore}"
echo
if build="$(ddi_ready "$ddi_dir")"; then
  echo "Image folder ready: $ddi_dir (developer disk image build $build)"
elif [ "${RCPAIR_DDI_FETCH:-1}" = 0 ]; then
  echo "Image folder $ddi_dir has no valid disk image and RCPAIR_DDI_FETCH=0, so it was left alone. See step 2."
elif [ "$(uname -s)" = Darwin ] && [ -f "$xcode_ddi/BuildManifest.plist" ]; then
  "$here/scripts/ddi-from-xcode.sh" || echo "Could not copy the disk image from Xcode. See step 2."
else
  "$here/scripts/ddi-from-github.sh" || echo "Could not pull the disk image. See step 2."
fi

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
  2) Disk image  Apple's Developer Disk Image has to be on the device. It goes on from your
                 image folder (printed above, ~/.rcpair/ddi unless RCPAIR_DDI_DIR is set).
                 Fill it yourself, recommended first:
                   scripts/ddi-from-xcode.sh                      on your Mac with Xcode
                   scripts/ddi-from-xcode.sh --mac you@your-mac   from here, over ssh
                 or pull a pinned public GitHub copy of Apple's file:
                   scripts/ddi-from-github.sh
                 Over USB, put it on the device with
                   idevice-tools --udid <UDID> cryptexd install-ddi ~/.rcpair/ddi
                 The pinned tools have no network command for this. A controller built on the
                 idevice library installs it from the same folder over its own tunnel, or
                 connect the device to Xcode on a Mac. A restart keeps it. An iOS update
                 removes it, so put it back the same way after each update.
  3) Drive       "hid"          tap / drag / type / buttons
                 "app_service"  launch / list apps
                 the device's developer screen stream to read the screen
  4) Decide      point your own agent or model at the tools above.
                 The decision layer (what to tap) is not in this repo. You bring it.

You provide: your device's address (on USB or the same LAN, mDNS finds it; across a
VPN use an explicit IP, mDNS does not cross a VPN), and Apple's Developer Disk Image in
your image folder (step 2).

Developer Mode must be on before you pair. You enable it with your passcode and the device reboots.
Some developer ports are refused on cellular.
EOF
