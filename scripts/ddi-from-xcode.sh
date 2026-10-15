#!/usr/bin/env bash
# Copies Apple's developer disk image from Xcode on your own Mac into the image folder.
# Run on the Mac, use --mac USER@HOST over ssh, or --from DIR for a Restore folder
# copied by hand with a USB stick, AirDrop, or a shared drive. This never downloads
# anything from the internet.
# RCPAIR_XCODE_DDI overrides /Library/Developer/DeveloperDiskImages/iOS_DDI/Restore.
# That path is pymobiledevice3's XCODE_DDI_RESTORE_DIR and is also named in
# idevice's cryptexd install-ddi help.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/ddi-lib.sh
source "$here/ddi-lib.sh"

usage() {
  cat <<'EOF'
Usage:
  scripts/ddi-from-xcode.sh [--dir DIR]                     on the Mac
  scripts/ddi-from-xcode.sh --mac USER@HOST [--dir DIR]     from another computer, over ssh
  scripts/ddi-from-xcode.sh --from RESTORE_DIR [--dir DIR]  from a copied Restore folder

--accept-unpinned skips the pinned hashes to review a new image; requires --dir to a separate folder.
EOF
}

dir=''
host=''
src=''
policy=xcode
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --accept-unpinned) policy=unpinned; shift ;;
    --dir|--mac|--from)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { usage >&2; exit 2; }
      case "$1" in
        --dir) dir="$2" ;;
        --mac) host="$2" ;;
        --from) src="$2" ;;
      esac
      shift 2
      ;;
    *) usage >&2; exit 2 ;;
  esac
done
if [ -n "$host" ] && [ -n "$src" ]; then
  usage >&2
  exit 2
fi

ddi_manifest pins "${RCPAIR_DDI_PINS:-$here/ddi-sha256.txt}" >/dev/null
if [ "$policy" = unpinned ]; then
  echo 'warning: --accept-unpinned skips the SHA-256 check against scripts/ddi-sha256.txt. Use it only to review a new upstream commit.' >&2
fi
target="$(ddi_target "$dir")"
ddi_review_target "$dir" "$policy"
ddi_repository_path "$target"
if [ -n "$src" ]; then
  ddi_repository_path "$src"
fi
xcode_path="${RCPAIR_XCODE_DDI:-/Library/Developer/DeveloperDiskImages/iOS_DDI/Restore}"
xcode_help="Xcode unpacks it there when it prepares a device: connect your iPhone or iPad to this Mac, open Xcode, and wait until Window > Devices and Simulators shows the device ready. Then run this again. No Mac? scripts/ddi-from-github.sh pulls the same files from a public GitHub copy."

if [ -n "$host" ]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  trap 'exit 1' HUP INT TERM
  remote_path="${xcode_path//\'/\'\\\'\'}"
  if ! ssh -- "$host" "tar -C '$remote_path' -cf - ." | tar -xf - -C "$tmp"; then
    ddi_fail "The Mac has no image at $xcode_path or could not be reached. $xcode_help"
  fi
  ddi_install "$tmp" "$target" "$policy"
elif [ -n "$src" ]; then
  ddi_install "$src" "$target" "$policy"
else
  if [ "$(uname -s)" != Darwin ]; then
    ddi_fail "This is not a Mac. Run it on your Mac, or from here with --mac USER@HOST to copy from a Mac over ssh, or --from DIR with a copied Restore folder."
  fi
  [ -f "$xcode_path/BuildManifest.plist" ] || ddi_fail "No developer disk image from Xcode at $xcode_path. $xcode_help"
  ddi_install "$xcode_path" "$target" "$policy"
fi
