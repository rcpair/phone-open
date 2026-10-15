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

ddi_review_target() {
  [ "$2" != unpinned ] || {
    [ -n "$1" ] && [ "$(ddi_target "$1")" != "$(ddi_target "")" ] ||
      ddi_fail "--accept-unpinned requires --dir to review into a separate folder, away from the default image folder"
  }
}

ddi_repository_path() {
  local path script_dir tree probe other
  path="$(ddi_absolute "$1")"
  script_dir="$(ddi_absolute "$(dirname "${BASH_SOURCE[0]}")")"
  tree="$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null)" || tree=''
  if [ -n "$tree" ]; then
    tree="$(ddi_absolute "$tree")"
    if [ "$tree/scripts" = "$script_dir" ] &&
        git -C "$tree" ls-files --error-unmatch -- scripts/ddi-lib.sh >/dev/null 2>&1; then
      case "$path/" in
        "$tree/"*) ddi_fail "image files must not live in the repository: $path" ;;
      esac
    fi
  fi
  probe="$path"
  while [ ! -d "$probe" ]; do
    probe="$(dirname "$probe")"
  done
  other="$(git -C "$probe" rev-parse --show-toplevel 2>/dev/null)" || return 0
  echo "warning: image folder is inside another git work tree: $path" >&2
}

ddi_stale_folders() {
  local old
  while IFS= read -r -d '' old; do
    if [ ! -e "$2" ] && [ ! -L "$2" ] &&
        ddi_manifest check "$old" xcode >/dev/null 2>&1; then
      mv "$old" "$2"
      break
    fi
  done < <(python3 -I - "$1" <<'PYTHON'
import os
import shutil
import stat
import sys
import time

parent = sys.argv[1]
old = []
if os.path.isdir(parent):
    for entry in os.scandir(parent):
        if entry.name.startswith((".ddi-new.", ".ddi-old.")):
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
                if entry.name.startswith(".ddi-old."):
                    old.append((info.st_mtime, entry.path))
                elif info.st_mtime < time.time() - 600:
                    shutil.rmtree(entry.path)
for _, path in sorted(old, reverse=True):
    sys.stdout.buffer.write(os.fsencode(path) + b"\0")
PYTHON
  )
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


def read_marker(folder):
    path = ".rcpair-source"
    if not os.path.lexists(os.path.join(folder, path)):
        return None
    filename = regular_file(folder, path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(filename, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("invalid .rcpair-source: not a regular file")
        data = stream.read(2049)
    if len(data) > 2048 or b"\0" in data or b"\r" in data:
        raise ValueError("invalid .rcpair-source: oversized marker, NUL or carriage return")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("invalid .rcpair-source: not UTF-8") from error
    lines = (text[:-1] if text.endswith("\n") else text).split("\n")
    marker = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if (not separator or key not in ("source", "build", "commit")
                or key in marker or not value or len(value) > 100):
            raise ValueError("invalid .rcpair-source: unknown or duplicate key, or invalid value")
        marker[key] = value
    origin = marker.get("source")
    if origin not in ("xcode", "github-pinned", "github-unpinned") or "build" not in marker:
        raise ValueError("invalid .rcpair-source: source and build are required")
    if origin == "xcode":
        if "commit" in marker:
            raise ValueError("invalid .rcpair-source: commit is for GitHub sources only")
    elif re.fullmatch(r"[0-9a-fA-F]{40}", marker.get("commit", "")) is None:
        raise ValueError("invalid .rcpair-source: GitHub source needs a 40-hex commit")
    return marker


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
    if not isinstance(build, str) or not build or len(build) > 100 or any(c in build for c in "\n\r\0"):
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
        if policy == "xcode" and build != pinned_build:
            print("note: Build " + build + " is not the pinned build " + pinned_build
                  + ". Accepted without a hash check against the pinned list, "
                  "because it came from Xcode.", file=sys.stderr)
        elif policy == "unpinned":
            if commit:
                print("# commit " + commit, file=sys.stderr)
            print("# build " + build, file=sys.stderr)
            for path, digest in hashes.items():
                print(digest + "  " + path, file=sys.stderr)
    return build, hashes


def replaceable_folder(folder):
    if not os.path.isdir(folder):
        fail("target is not a folder: " + folder)
    if not os.listdir(folder):
        return
    _, hashes = check_folder(folder, "unpinned")
    files = {os.path.normpath(path) for path in hashes} | {".rcpair-source"}
    directories = set()
    for path in hashes:
        parent = os.path.dirname(os.path.normpath(path))
        while parent:
            directories.add(parent)
            parent = os.path.dirname(parent)
    for root, dirs, names in os.walk(folder, onerror=lambda error: fail(str(error))):
        for name in dirs + names:
            filename = os.path.join(root, name)
            relative = os.path.relpath(filename, folder)
            info = os.lstat(filename)
            if not ((stat.S_ISDIR(info.st_mode) and relative in directories)
                    or (stat.S_ISREG(info.st_mode) and relative in files)):
                fail("refusing to replace a non-image folder: " + folder)


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
        if mode == "replaceable":
            replaceable_folder(folder)
            sys.exit(0)
        if mode == "check":
            marker = read_marker(folder)
            if marker is not None and marker["source"] == "xcode":
                policy = "xcode"
            elif marker is not None and marker["source"] == "github-pinned":
                policy = "strict"
            elif marker is None and all(os.path.lexists(os.path.join(folder, path))
                                        for path in pinned_hashes):
                policy = "strict"
            elif marker is not None and marker["source"] == "github-unpinned":
                fail("github-unpinned image folder is for review only and is not ready: " + folder)
            else:
                fail("image folder has no valid .rcpair-source marker or pinned file set: " + folder)
        if mode == "stage":
            _, hashes = check_folder(folder, policy)
            for path in hashes:
                filename = regular_file(folder, path)
                destination = os.path.join(staging, path)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copyfile(filename, destination)
            build, _ = check_folder(staging, policy, report=True, expected_hashes=hashes)
            origin = "xcode"
            if commit:
                origin = "github-unpinned" if policy == "unpinned" else "github-pinned"
            marker = "source=" + origin + "\nbuild=" + build + "\n"
            if commit:
                marker += "commit=" + commit + "\n"
            with open(os.path.join(staging, ".rcpair-source"), "w", encoding="utf-8") as stream:
                stream.write(marker)
            read_marker(staging)
        elif mode in ("check", "validate"):
            build, _ = check_folder(folder, policy, report=mode == "check")
            if mode == "check" and marker is not None and marker["build"] != build:
                fail(".rcpair-source build differs from BuildManifest.plist: " + folder)
        else:
            fail("unknown manifest check mode: " + mode)
        print(build)
except (OSError, ValueError) as error:
    fail(str(error))
PYTHON
}

ddi_ready() {
  if ddi_manifest check "$1" xcode; then
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
  parent="$(dirname "$target")"
  ddi_stale_folders "$parent" "$target"
  [ "$(ddi_absolute "$src")" != "$target" ] || ddi_fail "source and target are the same folder: $target"
  if [ -e "$target" ]; then
    ddi_manifest replaceable "$target" unpinned >/dev/null ||
      ddi_fail "refusing to replace a non-image folder: $target"
  fi

  ddi_manifest validate "$src" "$policy" "$source_description" "$commit" >/dev/null

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
  for backup in "$parent"/.ddi-old.*; do
    [ -d "$backup" ] && [ ! -L "$backup" ] && [ -O "$backup" ] || continue
    rm -rf "$backup"
  done
  old=''
  printf 'Image folder ready: %s (developer disk image build %s)\n' "$target" "$build"
  echo 'Next: put it on the device. See "The disk image" in docs/ARCHITECTURE.md.'
)
