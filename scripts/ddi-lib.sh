#!/usr/bin/env bash
# Shared validation and installation for the image folder scripts.
set -euo pipefail

ddi_fail() {
  echo "error: $*" >&2
  exit 1
}

ddi_absolute() {
  python3 -I -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

ddi_target() {
  ddi_absolute "${1:-${RCPAIR_DDI_DIR:-${RCPAIR_HOME:-$HOME/.rcpair}/ddi}}"
}

ddi_manifest() {
  local pins
  pins="${RCPAIR_DDI_PINS:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ddi-sha256.txt}"
  python3 -I - "$pins" "$@" <<'PYTHON'
import hashlib
import os
import plistlib
import re
import shutil
import stat
import sys


source = ""
commit = ""


def fail(message):
    if source:
        message += "\nSource: " + source
    if commit:
        message += "\nCommit: " + commit
    print("error: " + message + "\nRefusing to use these files. "
          "Nothing was written to the image folder.", file=sys.stderr)
    sys.exit(1)


def safe_path(path):
    return (isinstance(path, str) and bool(path) and not os.path.isabs(path)
            and ".." not in path.split("/") and not any(c in path for c in "\n\r\0"))


def read_pins(filename):
    pinned_commit = None
    build = None
    hashes = {}
    with open(filename, encoding="utf-8") as stream:
        for number, raw in enumerate(stream, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if line == "# commit" or line.startswith("# commit "):
                match = re.fullmatch(r"# commit ([0-9a-fA-F]{40})", line)
                if match is None or pinned_commit is not None:
                    fail("invalid or duplicate commit in pins file " + filename)
                pinned_commit = match[1]
            elif line == "# build" or line.startswith("# build "):
                match = re.fullmatch(r"# build (\S[^\r\n]*)", line)
                if match is None or build is not None:
                    fail("invalid or duplicate build in pins file " + filename)
                build = match[1]
            elif not line.startswith("#"):
                match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
                if match is None or not safe_path(match[2]):
                    fail("invalid pins line " + str(number) + " in " + filename)
                digest, path = match.groups()
                if path in hashes:
                    fail("duplicate path in pins file: " + path)
                hashes[path] = digest
    if pinned_commit is None or build is None:
        fail("pins file needs one # commit and one # build: " + filename)
    if len(hashes) != 5 or "BuildManifest.plist" not in hashes:
        fail("pins file must name BuildManifest.plist and four payloads: " + filename)
    return pinned_commit, build, hashes


def check_size(size, path, filename):
    limit = (8 if path == "BuildManifest.plist" else 64) * 1024 * 1024
    if size > limit:
        fail("file exceeds " + str(limit // (1024 * 1024)) + " MiB limit: " + filename)


def regular_file(folder, path, missing_ok=False):
    filename = os.path.join(folder, path)
    try:
        info = os.lstat(filename)
    except (FileNotFoundError, NotADirectoryError):
        if missing_ok:
            return None
        if path == "BuildManifest.plist":
            fail("no BuildManifest.plist in " + folder)
        fail("missing or empty payload file: " + filename)
    if not stat.S_ISREG(info.st_mode):
        fail("not a regular file (symlinks are refused): " + filename)
    root = os.path.realpath(folder)
    if os.path.commonpath((root, os.path.realpath(filename))) != root:
        fail("file is outside the source folder: " + filename)
    check_size(info.st_size, path, filename)
    return filename


def hash_file(folder, path):
    filename = regular_file(folder, path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(filename, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            fail("not a regular file: " + filename)
        check_size(info.st_size, path, filename)
        digest = hashlib.sha256()
        chunks = [] if path == "BuildManifest.plist" else None
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            check_size(size, path, filename)
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
    return digest.hexdigest(), b"".join(chunks) if chunks is not None else None


def mismatch(path, expected, actual):
    fail("SHA-256 mismatch for " + path + ": expected " + expected + "; actual " + actual)


def check_folder(folder, policy, report=False, expected_hashes=None):
    hashes = {}
    manifest_bytes = None
    if expected_hashes is not None or policy == "strict":
        for path, expected in (expected_hashes if expected_hashes is not None else pinned_hashes).items():
            if regular_file(folder, path, missing_ok=True) is None:
                actual = "missing"
            else:
                actual, data = hash_file(folder, path)
                hashes[path] = actual
                if path == "BuildManifest.plist":
                    manifest_bytes = data
            if actual != expected:
                mismatch(path, expected, actual)
        if expected_hashes is not None and policy == "strict":
            differences = [(path, expected, hashes.get(path, "missing"))
                           for path, expected in pinned_hashes.items()
                           if hashes.get(path) != expected]
            if differences:
                mismatch(*differences[0])

    if manifest_bytes is None:
        digest, manifest_bytes = hash_file(folder, "BuildManifest.plist")
        hashes["BuildManifest.plist"] = digest
    manifest_path = os.path.join(folder, "BuildManifest.plist")
    try:
        manifest = plistlib.loads(manifest_bytes)
    except Exception as error:
        fail("cannot read " + manifest_path + ": " + str(error))
    build = manifest.get("ProductBuildVersion", "unknown") if isinstance(manifest, dict) else "unknown"
    if not isinstance(build, str) or not build or any(c in build for c in "\n\r\0"):
        fail("invalid ProductBuildVersion in " + manifest_path)
    identities = manifest.get("BuildIdentities", []) if isinstance(manifest, dict) else []
    identity = None
    if isinstance(identities, list):
        for candidate in identities:
            info = candidate.get("Info", {}) if isinstance(candidate, dict) else {}
            variant = info.get("Variant", "") if isinstance(info, dict) else ""
            if isinstance(variant, str) and variant.endswith("Developer Disk Image Cryptex"):
                identity = candidate
                break
    if identity is None:
        fail(manifest_path + " has no 'Developer Disk Image Cryptex' build identity; "
             "this is not an iOS 17 or later developer disk image")

    paths = []
    for key in ("Cryptex1,GenericDmg", "Cryptex1,GenericTrustCache",
                "Cryptex1,CryptexInfoPlist", "Cryptex1,GenericVolume"):
        try:
            path = identity["Manifest"][key]["Info"]["Path"]
        except (KeyError, TypeError):
            fail(manifest_path + " is missing Info/Path for " + key)
        if not safe_path(path):
            fail(manifest_path + " has an unsafe payload path for " + key + ": " + repr(path))
        paths.append(path)
    files = ["BuildManifest.plist"] + paths
    if len(set(files)) != 5:
        fail(manifest_path + " must name four distinct payload files")
    if policy == "strict":
        if set(files) != set(pinned_hashes):
            fail("manifest paths differ from pins file: expected " + repr(sorted(pinned_hashes))
                 + "; actual " + repr(sorted(files)))
        if build != pinned_build:
            fail("ProductBuildVersion differs: expected " + pinned_build + "; actual " + build)
    for path in files:
        filename = regular_file(folder, path)
        if os.path.getsize(filename) == 0:
            fail("missing or empty payload file: " + filename)
        if path not in hashes:
            hashes[path], _ = hash_file(folder, path)
        if policy == "strict" and hashes[path] != pinned_hashes[path]:
            mismatch(path, pinned_hashes[path], hashes[path])
    pinned_payloads = {path: digest for path, digest in pinned_hashes.items()
                       if path != "BuildManifest.plist"}
    payloads_match = {hashes[path] for path in paths} == set(pinned_payloads.values())
    if policy == "xcode" and not payloads_match and build == pinned_build:
        fail("SHA-256 mismatch for " + ", ".join(paths) + " in pinned build " + pinned_build
             + "\nExpected payload hashes by file:\n"
             + "\n".join(digest + "  " + path for path, digest in pinned_payloads.items())
             + "\nActual payload hashes by file:\n"
             + "\n".join(hashes[path] + "  " + path for path in paths))
    hashes = {path: hashes[path] for path in (pinned_hashes if policy == "strict" else files)}
    if report:
        if policy == "xcode" and not payloads_match:
            print("note: build " + build + " from Xcode is not the pinned build " + pinned_build
                  + "; accepted because it came from Xcode on your Mac", file=sys.stderr)
        elif policy == "unpinned":
            if commit:
                print("# commit " + commit, file=sys.stderr)
            print("# build " + build, file=sys.stderr)
            for path, digest in hashes.items():
                print(digest + "  " + path, file=sys.stderr)
    return build, hashes


try:
    pins_file, mode, folder, *args = sys.argv[1:]
    if mode == "pins":
        pinned_commit, pinned_build, pinned_hashes = read_pins(folder)
        print("\n".join([pinned_commit, pinned_build] + list(pinned_hashes)))
    else:
        policy, *args = args
        if policy not in ("strict", "xcode", "unpinned"):
            fail("unknown image policy: " + policy)
        if mode == "stage":
            staging, *args = args
        source = args[0] if args else ""
        commit = args[1] if len(args) > 1 else ""
        pinned_commit, pinned_build, pinned_hashes = read_pins(pins_file)
        if mode == "stage":
            _, hashes = check_folder(folder, policy)
            for path in hashes:
                filename = regular_file(folder, path)
                destination = os.path.join(staging, path)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copyfile(filename, destination)
            build, _ = check_folder(staging, policy, report=True, expected_hashes=hashes)
        elif mode in ("check", "validate"):
            build, _ = check_folder(folder, policy, report=mode == "check")
        else:
            fail("unknown manifest check mode: " + mode)
        print(build)
except (OSError, ValueError) as error:
    fail(str(error))
PYTHON
}

ddi_ready() {
  if ddi_manifest check "$1" xcode 2>/dev/null; then
    return 0
  else
    return 1
  fi
}

ddi_install() (
  src="$(ddi_absolute "$1")"
  target="$(ddi_absolute "$2")"
  policy="$3"
  source_description="${4:-}"
  commit="${5:-}"
  [ "$(ddi_absolute "$src")" != "$target" ] || ddi_fail "source and target are the same folder: $target"
  if [ -e "$target" ]; then
    [ -d "$target" ] || ddi_fail "target is not a folder: $target"
    if [ ! -f "$target/BuildManifest.plist" ]; then
      entries="$(ls -A "$target")" || ddi_fail "cannot read target folder: $target"
      [ -z "$entries" ] || ddi_fail "refusing to replace a non-image folder: $target"
    fi
  fi

  ddi_manifest validate "$src" "$policy" "$source_description" "$commit" >/dev/null

  parent="$(dirname "$target")"
  rcpair_home="$(ddi_absolute "${RCPAIR_HOME:-$HOME/.rcpair}")"
  home_missing=false
  [ -e "$rcpair_home" ] || home_missing=true
  mkdir -p "$parent"
  if [ "$home_missing" = true ] && [ -d "$rcpair_home" ]; then
    chmod 700 "$rcpair_home"
  fi

  staging=''
  old=''
  ddi_cleanup() {
    if [ -n "$old" ] && [ -e "$old" ] && [ ! -e "$target" ]; then
      mv "$old" "$target"
    fi
    if [ -n "$staging" ]; then
      rm -rf "$staging"
    fi
  }
  trap ddi_cleanup EXIT
  trap 'exit 1' HUP INT TERM
  staging="$(mktemp -d "$parent/.ddi-new.XXXXXX")"
  build="$(ddi_manifest stage "$src" "$policy" "$staging" "$source_description" "$commit")"

  if [ -e "$target" ]; then
    old="$(mktemp -d "$parent/.ddi-old.XXXXXX")"
    rmdir "$old"
    mv "$target" "$old"
  fi
  mv "$staging" "$target"
  staging=''
  if [ -n "$old" ]; then
    rm -rf "$old"
    old=''
  fi
  printf 'Image folder ready: %s (developer disk image build %s)\n' "$target" "$build"
  echo 'Next: put it on the device. See "The disk image" in docs/ARCHITECTURE.md.'
)
