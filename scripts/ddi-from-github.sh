#!/usr/bin/env bash
# Pulls the same files from the public repository doronz88/DeveloperDiskImage.
# That repository is a third-party copy of Apple's file. Neither Apple nor this
# project publishes it. You run this, or scripts/connect.sh runs it when your image
# folder has no valid disk image (RCPAIR_DDI_FETCH=0 turns that off).
# The files must match the SHA-256 hashes kept with these scripts.
# Your phone still has to get the image personalized by Apple before it mounts it.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/ddi-lib.sh
source "$here/ddi-lib.sh"

usage() {
  cat <<'EOF'
Usage: scripts/ddi-from-github.sh [--dir DIR] [--ref REF] [--accept-unpinned]

REF is a branch, tag, or commit (default: the commit in scripts/ddi-sha256.txt).
--accept-unpinned skips the pinned hashes to review a new upstream commit; requires --dir to a separate folder.
RCPAIR_DDI_REPO overrides the repository URL.
EOF
}

dir=''
ref=''
policy=strict
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --accept-unpinned) policy=unpinned; shift ;;
    --dir|--ref)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { usage >&2; exit 2; }
      case "$1" in
        --dir) dir="$2" ;;
        --ref) ref="$2" ;;
      esac
      shift 2
      ;;
    *) usage >&2; exit 2 ;;
  esac
done

pins="$(ddi_manifest pins "${RCPAIR_DDI_PINS:-$here/ddi-sha256.txt}")"
if [ -z "$ref" ]; then
  ref="${pins%%$'\n'*}"
fi
if [ "$policy" = unpinned ]; then
  echo 'warning: --accept-unpinned skips the SHA-256 check against scripts/ddi-sha256.txt. Use it only to review a new upstream commit.' >&2
fi
command -v git >/dev/null 2>&1 || ddi_fail "git is required"
target="$(ddi_target "$dir")"
ddi_review_target "$dir" "$policy"
ddi_repository_path "$target"
repo="${RCPAIR_DDI_REPO:-https://github.com/doronz88/DeveloperDiskImage.git}"
cat >&2 <<'EOF'
Pulling Apple's developer disk image from github.com/doronz88/DeveloperDiskImage.
That is a third-party copy of Apple's file, not published by Apple or by this project.
The recommended source is Xcode on your own Mac: scripts/ddi-from-xcode.sh
EOF

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
trap 'exit 1' HUP INT TERM
git init -q "$tmp/repo"
git -C "$tmp/repo" remote add origin "$repo"
git -C "$tmp/repo" config core.sparseCheckout true
git -C "$tmp/repo" sparse-checkout set --no-cone /PersonalizedImages/Xcode_iOS_DDI_Cryptex/
if ! git -C "$tmp/repo" fetch --quiet --depth 1 --filter=blob:none origin "$ref"; then
  ddi_fail "git fetch failed; see the git error above"
fi
git -C "$tmp/repo" checkout --quiet FETCH_HEAD
commit="$(git -C "$tmp/repo" rev-parse FETCH_HEAD)"
echo "From $repo at commit $commit" >&2
ddi_install "$tmp/repo/PersonalizedImages/Xcode_iOS_DDI_Cryptex" "$target" "$policy" "$repo" "$commit"
