#!/usr/bin/env bash
# Fetch the public dependencies at pinned versions. This repo vendors NO third-party code and NO
# Apple binaries; it pulls the upstreams into ./third_party/ so you build against known versions.
#
# What it pulls:
#   - jkcoxson/idevice  (MIT)        the Linux CoreDevice tunnel and device tools (required)
#   - OpenMinis/iCTRL   (Apache-2.0) optional installed XCUITest runner, only with WITH_ICTRL=1
#
# It does NOT fetch Apple's Developer Disk Image. scripts/ddi-from-xcode.sh (from your own Mac) or
# scripts/ddi-from-github.sh (a pinned public copy) puts it in your image folder, and scripts/connect.sh
# runs one of them when that folder is empty.
set -euo pipefail

ICTRL_REF="${ICTRL_REF:-9fd0a7cc20819a1612f7d62ffbec5989d23f018e}"      # pinned commit; empty = default branch + warn
IDEVICE_REF="${IDEVICE_REF:-v0.1.68}"  # pinned tag

here="$(cd "$(dirname "$0")/.." && pwd)"
dest="$here/third_party"
mkdir -p "$dest"

clone() { # name url ref
  local name="$1" url="$2" ref="$3"
  if [ -d "$dest/$name/.git" ]; then
    echo "already present: $dest/$name"; return
  fi
  echo "cloning $name from $url"
  git clone -q "$url" "$dest/$name"
  if [ -n "$ref" ]; then
    git -C "$dest/$name" checkout -q --detach "$ref"
  else
    echo "WARNING: $name is not pinned to a version. Set ${name^^}_REF before relying on this." >&2
  fi
  git -C "$dest/$name" log -1 --format='  %H %ci %s'
}

clone idevice https://github.com/jkcoxson/idevice.git  "$IDEVICE_REF"
if [ "${WITH_ICTRL:-0}" = "1" ]; then
  clone ictrl https://github.com/OpenMinis/iCTRL.git "$ICTRL_REF"
else
  echo "Skipped optional iCTRL (installed-runner path). Set WITH_ICTRL=1 to include it."
fi

cat <<'NEXT'

Dependencies fetched into ./third_party/.
Next: turn on Developer Mode on YOUR device as RC Pair guides you, and pair. For Apple's
Developer Disk Image, run scripts/ddi-from-xcode.sh on your Mac (or with --mac you@your-mac from
here). Then run scripts/connect.sh to build the tools and walk tunnel -> drive. If your image folder
is still empty it fills it, from Xcode on this Mac if there is one, otherwise from a pinned public
GitHub copy. See docs/ARCHITECTURE.md.
NEXT
