#!/usr/bin/env python3
"""Tests for the image folder scripts and their shared manifest checks.

Run: python3 scripts/test_ddi_scripts.py

No network, no Apple files: it builds fake stand-ins in a temp folder. The scripts
run with a temporary home, and the repository source is a local file:// fixture.
"""
import hashlib
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest


HERE = Path(__file__).resolve().parent
XCODE = HERE / "ddi-from-xcode.sh"
GITHUB = HERE / "ddi-from-github.sh"
LIB = HERE / "ddi-lib.sh"
KEYS = ("Cryptex1,GenericDmg", "Cryptex1,GenericTrustCache",
        "Cryptex1,CryptexInfoPlist", "Cryptex1,GenericVolume")
PAYLOADS = {
    "Image.dmg": b"fake image",
    "Image.dmg.trustcache": b"fake trust cache",
    "Image.dmg.cryptex_info": b"fake cryptex info",
    "Image.dmg.root_hash": b"fake root hash",
}
XCODE_PAYLOADS = ("022-00001-001.dmg", "Firmware/022-00001-001.dmg.trustcache",
                  "Firmware/022-00001-001.dmg.cryptex_info", "Firmware/022-00001-001.dmg.root_hash")
NEXT_LINE = 'Next: put it on the device. See "The disk image" in docs/ARCHITECTURE.md.'


def fake_restore(folder):
    folder.mkdir(parents=True)
    manifest = {
        "ProductBuildVersion": "TEST1A",
        "BuildIdentities": [
            {"Info": {"Variant": "iOS Customer Developer Disk Image"}},
            {"Info": {"Variant": "iOS Customer Developer Disk Image Cryptex"},
             "Manifest": {key: {"Info": {"Path": path}}
                          for key, path in zip(KEYS, PAYLOADS)}},
        ],
    }
    with (folder / "BuildManifest.plist").open("wb") as stream:
        plistlib.dump(manifest, stream)
    for path, content in PAYLOADS.items():
        (folder / path).write_bytes(content)
    (folder / "Extra.txt").write_text("Do not copy this file.\n")
    return manifest


def folder_bytes(folder):
    return {str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()}


def installed_bytes(files, source="xcode", build="TEST1A", commit=None):
    marker = f"source={source}\nbuild={build}\n"
    if commit is not None:
        marker += f"commit={commit}\n"
    return {**files, ".rcpair-source": marker.encode()}


class DdiScriptsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".ddi-test-", dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = os.environ.copy()
        for name in ("RCPAIR_HOME", "RCPAIR_DDI_DIR", "RCPAIR_XCODE_DDI", "RCPAIR_DDI_REPO",
                     "RCPAIR_DDI_PINS"):
            self.env.pop(name, None)
        self.env.update(HOME=str(self.home), TMPDIR=str(self.root),
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_ALLOW_PROTOCOL="file", GIT_TERMINAL_PROMPT="0",
                        GIT_CEILING_DIRECTORIES=str(self.root))
        self.checkout = self.root / "script-repo"
        scripts = self.checkout / "scripts"
        scripts.mkdir(parents=True)
        for path in (XCODE, GITHUB, LIB, HERE / "ddi-sha256.txt"):
            shutil.copy2(path, scripts / path.name)
        shutil.copy2(HERE.parent / ".gitignore", self.checkout / ".gitignore")
        if shutil.which("git"):
            self.git("init", "--quiet", self.checkout)
        self.lib = scripts / LIB.name
        self.source = self.root / "Restore folder's"
        self.manifest = fake_restore(self.source)
        self.expected = folder_bytes(self.source)
        del self.expected["Extra.txt"]
        self.target = self.home / ".rcpair" / "ddi"
        self.pins = self.root / "pins.txt"
        self.write_pins()
        self.env["RCPAIR_DDI_PINS"] = str(self.pins)

    def write_pins(self, commit="0" * 40):
        lines = [f"# commit {commit}", "# build TEST1A"]
        for path, content in self.expected.items():
            lines.append(f"{hashlib.sha256(content).hexdigest()}  {path}")
        self.pins.write_text("\n".join(lines) + "\n")

    def run_lib(self, mode, *args, env=None):
        return subprocess.run(["bash", "-c", 'source "$1"; shift; "$@"',
                               "bash", str(self.lib), mode, *map(str, args)],
                              env=env or self.env, capture_output=True, text=True, timeout=30)

    def run_script(self, script, *args, env=None):
        script = self.checkout / "scripts" / script.name
        return subprocess.run([str(script), *map(str, args)], env=env or self.env,
                              capture_output=True, text=True, timeout=30)

    def assert_ready(self, result, target, source="xcode", commit=None):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(folder_bytes(target), installed_bytes(self.expected, source, commit=commit))
        self.assertEqual(result.stdout.splitlines()[-2:], [
            f"Image folder ready: {target.resolve()} (developer disk image build TEST1A)",
            NEXT_LINE,
        ])

    def git(self, *args):
        result = subprocess.run(["git", *map(str, args)], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def fixture_repo(self):
        repo = self.root / "fixture-repo"
        self.git("init", "--quiet", repo)
        self.git("-C", repo, "symbolic-ref", "HEAD", "refs/heads/main")
        image = repo / "PersonalizedImages" / "Xcode_iOS_DDI_Cryptex"
        shutil.copytree(self.source, image, symlinks=True)
        (image / "Extra.txt").unlink()
        unrelated = repo / "Unrelated"
        unrelated.mkdir()
        (unrelated / "skip.txt").write_text("Skip this folder.\n")
        self.git("-C", repo, "add", ".")
        self.git("-C", repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fake image fixture")
        self.git("-C", repo, "config", "uploadpack.allowFilter", "true")
        self.env["RCPAIR_DDI_REPO"] = repo.as_uri()
        sha = self.git("-C", repo, "rev-parse", "HEAD")
        self.write_pins(sha)
        return sha

    def commit_fixture(self):
        repo = self.root / "fixture-repo"
        self.git("-C", repo, "add", ".")
        self.git("-C", repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Changed fixture")
        return self.git("-C", repo, "rev-parse", "HEAD")

    def write_manifest(self):
        with (self.source / "BuildManifest.plist").open("wb") as stream:
            plistlib.dump(self.manifest, stream)

    def xcode_fixture(self):
        for key, old, path in zip(KEYS, PAYLOADS, XCODE_PAYLOADS):
            self.manifest["BuildIdentities"][1]["Manifest"][key]["Info"]["Path"] = path
            destination = self.source / path
            destination.parent.mkdir(exist_ok=True)
            (self.source / old).rename(destination)
        self.write_manifest()
        (self.source / "Extra.txt").unlink()
        expected = folder_bytes(self.source)
        for path in ("022-00002-001.dmg", "Firmware/022-00002-001.dmg.trustcache",
                     "Restore.plist"):
            (self.source / path).write_bytes(b"Do not copy this file.")
        return expected

    def test_xcode_default_folder(self):
        result = self.run_script(XCODE, "--from", self.source)
        self.assert_ready(result, self.target)
        self.assertEqual(len(folder_bytes(self.target)), 6)
        self.assertEqual(stat.S_IMODE(self.target.parent.stat().st_mode), 0o700)

    def test_xcode_apple_paths_match_pinned_payloads(self):
        expected = self.xcode_fixture()
        self.assertNotEqual(expected["BuildManifest.plist"], self.expected["BuildManifest.plist"])
        result = self.run_script(XCODE, "--from", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(folder_bytes(self.target), installed_bytes(expected))
        self.assertEqual(set(expected), {"BuildManifest.plist", *XCODE_PAYLOADS})
        for mode, args in (("ddi_ready", (self.target,)),
                           ("ddi_manifest", ("check", self.target, "xcode"))):
            with self.subTest(mode=mode):
                result = self.run_lib(mode, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "TEST1A\n")
                self.assertEqual(result.stderr, "")

    def test_xcode_apple_paths_changed_pinned_payload_fails(self):
        expected = self.xcode_fixture()
        path = XCODE_PAYLOADS[0]
        original = expected[path]
        changed = bytes([original[0] ^ 1]) + original[1:]
        (self.source / path).write_bytes(changed)
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertIn("pinned build TEST1A", result.stderr)
        self.assertIn("Expected payload hashes by file:", result.stderr)
        self.assertIn("Actual payload hashes by file:", result.stderr)
        for name, content in PAYLOADS.items():
            self.assertIn(hashlib.sha256(content).hexdigest() + "  " + name, result.stderr)
        expected[path] = changed
        for name in XCODE_PAYLOADS:
            self.assertIn(hashlib.sha256(expected[name]).hexdigest() + "  " + name, result.stderr)
        self.assertFalse(self.target.parent.exists())

    def test_xcode_apple_paths_other_build(self):
        self.xcode_fixture()
        self.manifest["ProductBuildVersion"] = "OTHER9"
        self.write_manifest()
        (self.source / XCODE_PAYLOADS[0]).write_bytes(b"different image")
        expected = {path: (self.source / path).read_bytes()
                    for path in ("BuildManifest.plist", *XCODE_PAYLOADS)}
        result = self.run_script(XCODE, "--from", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(folder_bytes(self.target), installed_bytes(expected, build="OTHER9"))
        self.assertIn("Build OTHER9 is not the pinned build TEST1A. Accepted without a hash check "
                      "against the pinned list, because it came from Xcode.", result.stderr)
        self.assertEqual(result.stderr.count("note: Build"), 1)
        self.assertEqual(self.run_lib("ddi_ready", self.target).returncode, 0)

    def test_xcode_apple_paths_other_build_pinned_payloads(self):
        self.xcode_fixture()
        self.manifest["ProductBuildVersion"] = "OTHER9"
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Build OTHER9 is not the pinned build TEST1A.", result.stderr)

    def test_xcode_firmware_symlink_outside_source_is_refused(self):
        self.xcode_fixture()
        firmware = self.source / "Firmware"
        outside = self.root / "outside"
        firmware.rename(outside)
        firmware.symlink_to(outside, target_is_directory=True)
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the source folder", result.stderr)
        self.assertIn("Firmware/", result.stderr)
        self.assertFalse(self.target.parent.exists())

    def test_sparse_oversized_payload_is_refused(self):
        with (self.source / "Image.dmg").open("wb") as stream:
            stream.truncate(65 * 1024 * 1024)
        for policy in ("strict", "xcode", "unpinned"):
            with self.subTest(policy=policy):
                result = self.run_lib("ddi_manifest", "validate", self.source, policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("file exceeds 64 MiB limit", result.stderr)
                self.assertIn("Image.dmg", result.stderr)
                self.assertNotIn("SHA-256 mismatch", result.stderr)
        self.assertFalse(self.target.parent.exists())

    def test_oversized_manifest_is_refused(self):
        for size in (8 * 1024 * 1024 + 1, 65 * 1024 * 1024):
            with (self.source / "BuildManifest.plist").open("wb") as stream:
                stream.truncate(size)
            for policy in ("strict", "xcode", "unpinned"):
                with self.subTest(size=size, policy=policy):
                    result = self.run_lib("ddi_manifest", "validate", self.source, policy)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("file exceeds 8 MiB limit", result.stderr)
                    self.assertIn("BuildManifest.plist", result.stderr)
                    self.assertNotIn("SHA-256 mismatch", result.stderr)
                    self.assertNotIn("cannot read", result.stderr)
        self.assertFalse(self.target.parent.exists())

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_local_repo(self):
        sha = self.fixture_repo()
        result = self.run_script(GITHUB)
        self.assert_ready(result, self.target, "github-pinned", sha)
        self.assertIn("third-party copy", result.stderr)
        self.assertIn(f"From {self.env['RCPAIR_DDI_REPO']} at commit {sha}", result.stderr)
        self.assertEqual(sorted(self.root.iterdir()),
                         sorted([self.home, self.source, self.pins, self.checkout,
                                 self.root / "fixture-repo"]))

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_full_sha(self):
        sha = self.fixture_repo()
        self.assert_ready(self.run_script(GITHUB, "--ref", sha), self.target, "github-pinned", sha)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_both_sources_identical(self):
        sha = self.fixture_repo()
        first = self.root / "A"
        second = self.root / "B"
        xcode = self.run_script(XCODE, "--from", self.source, "--dir", first)
        github = self.run_script(GITHUB, "--dir", second)
        self.assert_ready(xcode, first)
        self.assert_ready(github, second, "github-pinned", sha)
        self.assertEqual({k: v for k, v in folder_bytes(first).items() if k != ".rcpair-source"},
                         {k: v for k, v in folder_bytes(second).items() if k != ".rcpair-source"})
        self.assertEqual(xcode.stdout.replace(str(first), "TARGET"),
                         github.stdout.replace(str(second), "TARGET"))

    def test_override_order(self):
        moved_home = self.root / "custom-home"
        env_dir = self.root / "env-dir"
        explicit = self.root / "explicit-dir"
        self.env["RCPAIR_HOME"] = str(moved_home)
        self.assert_ready(self.run_script(XCODE, "--from", self.source), moved_home / "ddi")
        self.assertEqual(stat.S_IMODE(moved_home.stat().st_mode), 0o700)
        self.env["RCPAIR_DDI_DIR"] = str(env_dir)
        self.assert_ready(self.run_script(XCODE, "--from", self.source), env_dir)
        self.assert_ready(self.run_script(XCODE, "--from", self.source, "--dir", explicit), explicit)
        self.assertFalse(self.target.exists())

    def test_failed_fill_preserves_old_folder(self):
        self.assert_ready(self.run_script(XCODE, "--from", self.source), self.target)
        (self.source / "Image.dmg.root_hash").unlink()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(folder_bytes(self.target), installed_bytes(self.expected))
        self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])

    def test_successful_refill_replaces_old_folder(self):
        self.target = self.root / "review"
        self.assert_ready(self.run_script(XCODE, "--from", self.source, "--dir", self.target), self.target)
        (self.target / "stale.txt").write_bytes(b"old extra file")
        (self.source / "Image.dmg").write_bytes(b"new fake image")
        self.expected["Image.dmg"] = b"new fake image"
        self.assert_ready(self.run_script(XCODE, "--from", self.source, "--accept-unpinned",
                                          "--dir", self.target), self.target)
        self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])

    def test_failed_swap_restores_old_folder(self):
        self.target = self.root / "review"
        self.assert_ready(self.run_script(XCODE, "--from", self.source, "--dir", self.target), self.target)
        (self.source / "Image.dmg").write_bytes(b"new fake image")
        real_mv = shutil.which("mv")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_mv = fake_bin / "mv"
        fake_mv.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                           'case "$1" in */.ddi-new.*) exit 1 ;; esac\n'
                           'exec "$DDI_TEST_MV" "$@"\n')
        fake_mv.chmod(0o755)
        self.env["DDI_TEST_MV"] = real_mv
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env["PATH"]
        result = self.run_script(XCODE, "--from", self.source, "--accept-unpinned", "--dir", self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(folder_bytes(self.target), installed_bytes(self.expected))
        self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])

    def test_refuses_non_image_target(self):
        target = self.root / "keep"
        target.mkdir()
        (target / "keep.txt").write_bytes(b"keep me")
        result = self.run_script(XCODE, "--from", self.source, "--dir", target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-image folder", result.stderr)
        self.assertEqual(folder_bytes(target), {"keep.txt": b"keep me"})

    def test_rejects_unsafe_path_and_missing_identity(self):
        self.manifest["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"] = "../evil"
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source, "--accept-unpinned",
                                 "--dir", self.root / "review")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe payload path", result.stderr)
        self.assertFalse(self.target.parent.exists())
        self.manifest["BuildIdentities"].pop()
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source, "--accept-unpinned",
                                 "--dir", self.root / "review")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no 'Developer Disk Image Cryptex' build identity", result.stderr)
        self.assertFalse(self.target.parent.exists())

    def test_missing_local_xcode_image(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.env["RCPAIR_XCODE_DDI"] = str(empty)
        result = self.run_script(XCODE)
        self.assertNotEqual(result.returncode, 0)
        if platform.system() == "Darwin":
            self.assertIn("Xcode", result.stderr)
            self.assertIn("ddi-from-github.sh", result.stderr)
            self.assertIn("Devices and Simulators", result.stderr)
        else:
            self.assertIn("This is not a Mac", result.stderr)
            self.assertIn("--mac", result.stderr)

    def test_remote_mac(self):
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        ssh = fake_bin / "ssh"
        ssh.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                       '[ "$#" -eq 3 ]\n[ "$1" = -- ]\n[ "$2" = fakehost ]\n'
                       'shift 2\nexec bash -c "$1"\n')
        ssh.chmod(0o755)
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env["PATH"]
        self.env["RCPAIR_XCODE_DDI"] = str(self.source)
        self.assert_ready(self.run_script(XCODE, "--mac", "fakehost"), self.target)
        self.assertEqual(sorted(self.root.iterdir()),
                         sorted([self.home, self.source, self.pins, self.checkout, fake_bin]))

    def test_help_and_unknown_flag(self):
        for script in (XCODE, GITHUB):
            with self.subTest(script=script.name):
                for flag in ("-h", "--help"):
                    result = self.run_script(script, flag)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Usage:", result.stdout)
                result = self.run_script(script, "--unknown")
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage:", result.stderr)

    def test_rejects_conflicting_or_missing_arguments(self):
        result = self.run_script(XCODE, "--mac", "fakehost", "--from", self.source)
        self.assertEqual(result.returncode, 2)
        for script, flag in ((XCODE, "--dir"), (XCODE, "--from"), (XCODE, "--mac"),
                             (GITHUB, "--dir"), (GITHUB, "--ref")):
            with self.subTest(script=script.name, flag=flag):
                self.assertEqual(self.run_script(script, flag).returncode, 2)

    def test_nested_payload_paths(self):
        paths = self.manifest["BuildIdentities"][1]["Manifest"]
        nested = self.source / "payloads"
        nested.mkdir()
        for key, path in zip(KEYS, PAYLOADS):
            paths[key]["Info"]["Path"] = "payloads/" + path
            (self.source / path).rename(nested / path)
        self.write_manifest()
        self.expected = folder_bytes(self.source)
        del self.expected["Extra.txt"]
        self.write_pins()
        self.assert_ready(self.run_script(XCODE, "--from", self.source), self.target)

    def test_refuses_same_source_and_target(self):
        before = folder_bytes(self.source)
        result = self.run_script(XCODE, "--from", self.source, "--dir", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("same folder", result.stderr)
        self.assertEqual(folder_bytes(self.source), before)

    def test_manifest_check_errors_and_unknown_build(self):
        for bad_path in ("/absolute", "", "folder/../evil"):
            with self.subTest(path=bad_path):
                self.manifest["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"] = bad_path
                self.write_manifest()
                self.assertNotEqual(self.run_script(XCODE, "--from", self.source).returncode, 0)
                self.assertFalse(self.target.exists())
        self.manifest["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"] = "Image.dmg"
        del self.manifest["ProductBuildVersion"]
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("developer disk image build unknown", result.stdout)
        (self.source / "Image.dmg").write_bytes(b"")
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or empty payload file", result.stderr)
        self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])
        del self.manifest["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"]
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing Info/Path", result.stderr)
        (self.source / "BuildManifest.plist").write_bytes(b"invalid plist")
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read", result.stderr)
        (self.source / "BuildManifest.plist").unlink()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no BuildManifest.plist", result.stderr)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_defaults_to_pinned_commit(self):
        first = self.fixture_repo()
        image = self.root / "fixture-repo/PersonalizedImages/Xcode_iOS_DDI_Cryptex/Image.dmg"
        image.write_bytes(b"second commit image")
        second = self.commit_fixture()
        self.assertNotEqual(first, second)
        result = self.run_script(GITHUB)
        self.assert_ready(result, self.target, "github-pinned", first)
        self.assertIn(f"at commit {first}", result.stderr)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_wrong_pin_preserves_old_folder(self):
        sha = self.fixture_repo()
        self.assert_ready(self.run_script(GITHUB), self.target, "github-pinned", sha)
        path = "Image.dmg"
        actual = hashlib.sha256(self.expected[path]).hexdigest()
        wrong = "0" * 64
        self.pins.write_text(self.pins.read_text().replace(actual, wrong))
        result = self.run_script(GITHUB)
        self.assertNotEqual(result.returncode, 0)
        for message in (path, actual, wrong, self.env["RCPAIR_DDI_REPO"], sha,
                        "Refusing to use these files. Nothing was written to the image folder."):
            self.assertIn(message, result.stderr)
        self.assertEqual(folder_bytes(self.target), installed_bytes(self.expected, "github-pinned", commit=sha))
        self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_new_ref_requires_override(self):
        self.fixture_repo()
        image = self.root / "fixture-repo/PersonalizedImages/Xcode_iOS_DDI_Cryptex/Image.dmg"
        image.write_bytes(b"second commit image")
        second = self.commit_fixture()
        result = self.run_script(GITHUB, "--ref", second)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch for Image.dmg", result.stderr)
        self.assertFalse(self.target.exists())
        self.target = self.root / "review"
        result = self.run_script(GITHUB, "--ref", second, "--accept-unpinned", "--dir", self.target)
        self.expected["Image.dmg"] = image.read_bytes()
        self.assert_ready(result, self.target, "github-unpinned", second)
        self.assertIn("warning: --accept-unpinned skips the SHA-256 check", result.stderr)
        self.assertIn(f"# commit {second}", result.stderr)
        self.assertIn("# build TEST1A", result.stderr)
        hashes = re.findall(r"^([0-9a-f]{64})  (.+)$", result.stderr, re.MULTILINE)
        self.assertEqual(dict((path, digest) for digest, path in hashes),
                         {path: hashlib.sha256(content).hexdigest()
                          for path, content in self.expected.items()})

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_branch_and_tag_refs(self):
        sha = self.fixture_repo()
        self.git("-C", self.root / "fixture-repo", "tag", "fixture-tag", sha)
        for ref in ("main", "fixture-tag"):
            with self.subTest(ref=ref):
                self.assert_ready(self.run_script(GITHUB, "--ref", ref), self.target, "github-pinned", sha)

    def test_xcode_other_build(self):
        self.manifest["ProductBuildVersion"] = "OTHER9"
        self.write_manifest()
        (self.source / "Image.dmg").write_bytes(b"different image")
        result = self.run_script(XCODE, "--from", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = dict(self.expected)
        expected["BuildManifest.plist"] = (self.source / "BuildManifest.plist").read_bytes()
        expected["Image.dmg"] = b"different image"
        self.assertEqual(folder_bytes(self.target), installed_bytes(expected, build="OTHER9"))
        self.assertIn("Build OTHER9 is not the pinned build TEST1A. Accepted without a hash check "
                      "against the pinned list, because it came from Xcode.", result.stderr)
        self.assertEqual(result.stderr.count("note: Build"), 1)
        result = self.run_lib("ddi_ready", self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OTHER9\n")

    def test_xcode_changed_pinned_build_requires_override(self):
        (self.source / "Image.dmg").write_bytes(b"changed fake image")
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch for Image.dmg", result.stderr)
        self.assertFalse(self.target.exists())
        self.expected["Image.dmg"] = b"changed fake image"
        self.target = self.root / "review"
        result = self.run_script(XCODE, "--from", self.source, "--accept-unpinned", "--dir", self.target)
        self.assert_ready(result, self.target)
        self.assertIn("warning: --accept-unpinned skips the SHA-256 check", result.stderr)
        self.assertNotIn("# commit", result.stderr)
        self.assertIn("# build TEST1A", result.stderr)
        self.assertEqual(len(re.findall(r"^[0-9a-f]{64}  .+$", result.stderr, re.MULTILINE)), 5)

    def test_real_pins_parse_and_default_is_relative_to_lib(self):
        result = self.run_lib("ddi_manifest", "pins", HERE / "ddi-sha256.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        commit, build, *paths = result.stdout.splitlines()
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(commit, "6eae353ae694bda1c421d4a3eee5459ae59c99a1")
        self.assertEqual(build, "27A5228h")
        self.assertEqual(set(paths), {"BuildManifest.plist", *PAYLOADS})
        self.assertEqual(len(paths), 5)
        env = self.env.copy()
        del env["RCPAIR_DDI_PINS"]
        result = subprocess.run(["bash", "-c", 'source "$1"; ddi_manifest check "$2" strict',
                                 "bash", str(self.lib), str(self.source)], cwd=self.root, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch for BuildManifest.plist", result.stderr)
        self.assertIn("27385d7582b03b36bb3104e22b520aee0c47d72fecb4e8ecfe12ef5d966c7012",
                      result.stderr)

    def test_malformed_pins_fail_both_scripts(self):
        good = self.pins.read_text()
        lines = good.splitlines()
        malformed = [
            "\n".join(lines[1:]),
            "\n".join(line for line in lines if not line.startswith("# build ")),
            good.replace(lines[2], "bad hash  BuildManifest.plist"),
            good + lines[0] + "\n",
            good + lines[1] + "\n",
            good + lines[2] + "\n",
            good.replace(lines[2], lines[2].replace("  ", " ")),
            good.replace(lines[2], lines[2].upper()),
            good.replace("  Image.dmg\n", "  ../Image.dmg\n"),
        ]
        self.assert_ready(self.run_script(XCODE, "--from", self.source), self.target)
        for content in malformed:
            self.pins.write_text(content)
            for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
                for override in ((), ("--accept-unpinned",)):
                    with self.subTest(content=content, script=script.name, override=override):
                        extra = ("--dir", self.root / "review") if override else ()
                        result = self.run_script(script, *args, *override, *extra)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("pins", result.stderr)
                        self.assertEqual(folder_bytes(self.target), installed_bytes(self.expected))
                        self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_ddi_ready_both_sources_and_broken_folders(self):
        sha = self.fixture_repo()
        for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
            with self.subTest(script=script.name):
                self.assert_ready(self.run_script(script, *args), self.target,
                                  "xcode" if script == XCODE else "github-pinned",
                                  None if script == XCODE else sha)
                result = self.run_lib("ddi_ready", self.target)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "TEST1A\n")
                (self.target / "Image.dmg").write_bytes(b"changed")
                result = self.run_lib("ddi_ready", self.target)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("SHA-256 mismatch", result.stderr)
        empty = self.root / "empty"
        empty.mkdir()
        for folder in (empty, self.root / "missing"):
            result = self.run_lib("ddi_ready", folder)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("no valid .rcpair-source marker or pinned file set", result.stderr)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_hashes_manifest_before_parsing(self):
        (self.source / "BuildManifest.plist").write_bytes(b"not a plist")
        sha = self.fixture_repo()
        result = self.run_script(GITHUB)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch for BuildManifest.plist", result.stderr)
        self.assertIn(sha, result.stderr)
        self.assertNotIn("cannot read", result.stderr)
        self.assertNotIn("Invalid file", result.stderr)
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_flipped_payload_fails_both_scripts(self):
        original = self.expected["Image.dmg"]
        (self.source / "Image.dmg").write_bytes(bytes([original[0] ^ 1]) + original[1:])
        self.fixture_repo()
        for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
            with self.subTest(script=script.name):
                result = self.run_script(script, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SHA-256 mismatch for Image.dmg", result.stderr)
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_symlink_payload_fails_both_scripts(self):
        outside = self.root / "outside.dmg"
        outside.write_bytes(self.expected["Image.dmg"])
        image = self.source / "Image.dmg"
        image.unlink()
        image.symlink_to(outside)
        self.fixture_repo()
        for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
            for override in ((), ("--accept-unpinned",)):
                with self.subTest(script=script.name, override=override):
                    extra = ("--dir", self.root / "review") if override else ()
                    result = self.run_script(script, *args, *override, *extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("not a regular file", result.stderr)
                    self.assertIn("Image.dmg", result.stderr)
                    self.assertFalse(self.target.exists())
        self.assertEqual(outside.read_bytes(), self.expected["Image.dmg"])

    def test_unpinned_traversal_and_absolute_paths(self):
        outside = self.root / "outside.dmg"
        outside.write_bytes(self.expected["Image.dmg"])
        self.manifest["ProductBuildVersion"] = "OTHER9"
        for path in ("../outside.dmg", str(outside)):
            self.manifest["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"] = path
            self.write_manifest()
            for override in ((), ("--accept-unpinned",)):
                with self.subTest(path=path, override=override):
                    extra = ("--dir", self.root / "review") if override else ()
                    result = self.run_script(XCODE, "--from", self.source, *override, *extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unsafe payload path", result.stderr)
                    self.assertFalse(self.target.exists())

    def test_manifest_symlink_and_payload_directory_are_refused(self):
        manifest = self.source / "BuildManifest.plist"
        outside = self.root / "outside.plist"
        manifest.rename(outside)
        manifest.symlink_to(outside)
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular file", result.stderr)
        manifest.unlink()
        outside.rename(manifest)
        image = self.source / "Image.dmg"
        image.unlink()
        image.mkdir()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular file", result.stderr)
        self.assertFalse(self.target.exists())

    def test_parent_symlink_outside_source_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "Image.dmg").write_bytes(self.expected["Image.dmg"])
        (self.source / "payloads").symlink_to(outside, target_is_directory=True)
        self.manifest["ProductBuildVersion"] = "OTHER9"
        self.manifest["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"] = "payloads/Image.dmg"
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the source folder", result.stderr)
        self.assertFalse(self.target.exists())

    def test_unpinned_requires_explicit_separate_folder(self):
        self.target.mkdir(parents=True)
        alias = self.root / "default-alias"
        alias.symlink_to(self.target, target_is_directory=True)
        for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
            for target_args in ((), ("--dir", self.target), ("--dir", alias),
                                ("--dir", self.target / ".." / "ddi")):
                with self.subTest(script=script.name, args=target_args):
                    result = self.run_script(script, *args, "--accept-unpinned", *target_args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("review into a separate folder", result.stderr)
                    self.assertEqual(folder_bytes(self.target), {})
                    self.assertNotIn("Pulling", result.stderr)
        for name, default in (("RCPAIR_HOME", self.root / "custom-home"),
                              ("RCPAIR_DDI_DIR", self.root / "custom-ddi")):
            env = self.env.copy()
            env[name] = str(default)
            target = default / "ddi" if name == "RCPAIR_HOME" else default
            for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
                result = self.run_script(script, *args, "--accept-unpinned", "--dir", target, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("review into a separate folder", result.stderr)
                self.assertFalse(target.exists())

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_github_unpinned_folder_is_never_ready(self):
        sha = self.fixture_repo()
        review = self.root / "review"
        result = self.run_script(GITHUB, "--accept-unpinned", "--dir", review)
        self.assert_ready(result, review, "github-unpinned", sha)
        for mode, args in (("ddi_ready", (review,)),
                           ("ddi_manifest", ("check", review, "xcode")),
                           ("ddi_manifest", ("check", review, "strict"))):
            result = self.run_lib(mode, *args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("github-unpinned", result.stderr)
            self.assertIn("review only", result.stderr)
            self.assertEqual(result.stdout, "")
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_ready_hashes_pinned_manifest_before_parsing(self):
        sha = self.fixture_repo()
        self.assert_ready(self.run_script(GITHUB), self.target, "github-pinned", sha)
        for marked in (True, False):
            if not marked:
                (self.target / ".rcpair-source").unlink()
            for content in (b"not a plist", self.expected["BuildManifest.plist"].replace(b"TEST1A", b"OTHER9")):
                (self.target / "BuildManifest.plist").write_bytes(content)
                for mode, args in (("ddi_ready", (self.target,)),
                                   ("ddi_manifest", ("check", self.target, "xcode"))):
                    with self.subTest(marked=marked, content=content, mode=mode):
                        result = self.run_lib(mode, *args)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("SHA-256 mismatch for BuildManifest.plist", result.stderr)
                        self.assertNotIn("cannot read", result.stderr)
            (self.target / "BuildManifest.plist").write_bytes(self.expected["BuildManifest.plist"])
            result = self.run_lib("ddi_ready", self.target)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ready_refuses_unmarked_xcode_layout(self):
        self.xcode_fixture()
        self.assertEqual(self.run_script(XCODE, "--from", self.source).returncode, 0)
        (self.target / ".rcpair-source").unlink()
        result = self.run_lib("ddi_ready", self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no valid .rcpair-source marker or pinned file set", result.stderr)

    def test_invalid_provenance_markers_are_not_ready(self):
        self.assert_ready(self.run_script(XCODE, "--from", self.source), self.target)
        good = b"source=xcode\nbuild=TEST1A\n"
        invalid = [
            b"", b"source=xcode\n", b"build=TEST1A\n", b"source=other\nbuild=TEST1A\n",
            good + b"extra=value\n", good + b"source=xcode\n", good + b"build=TEST1A\n",
            good + b"commit=" + b"0" * 40 + b"\n", good.replace(b"TEST1A", b"x" * 101),
            good.replace(b"TEST1A", b"TEST\0"), good.replace(b"TEST1A", b"\xff"),
            good.replace(b"TEST1A", b""), good + b"\n", good.replace(b"\n", b"\r\n"),
            good.replace(b"source=xcode", b"source=github-pinned"),
            b"source=github-pinned\nbuild=TEST1A\ncommit=not-a-sha\n",
            b"source=github-unpinned\nbuild=TEST1A\ncommit=" + b"g" * 40 + b"\n",
            b"x" * 2049,
        ]
        for marker in invalid:
            (self.target / ".rcpair-source").write_bytes(marker)
            for mode, args in (("ddi_ready", (self.target,)),
                               ("ddi_manifest", ("check", self.target, "xcode"))):
                with self.subTest(marker=marker, mode=mode):
                    result = self.run_lib(mode, *args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(".rcpair-source", result.stderr)
                    self.assertEqual(result.stdout, "")

    def test_marker_symlink_and_build_mismatch_are_refused(self):
        self.assert_ready(self.run_script(XCODE, "--from", self.source), self.target)
        marker = self.target / ".rcpair-source"
        marker.write_text("source=xcode\nbuild=OTHER9\n")
        result = self.run_lib("ddi_ready", self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build differs", result.stderr)
        outside = self.root / "marker"
        marker.rename(outside)
        marker.symlink_to(outside)
        result = self.run_lib("ddi_ready", self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular file", result.stderr)

    def test_marker_value_length_limit(self):
        self.manifest["ProductBuildVersion"] = "B" * 100
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_lib("ddi_ready", self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "B" * 100 + "\n")
        before = folder_bytes(self.target)
        self.manifest["ProductBuildVersion"] += "B"
        self.write_manifest()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid ProductBuildVersion", result.stderr)
        self.assertEqual(folder_bytes(self.target), before)

    def test_refuses_manifest_target_with_unrelated_entries(self):
        shutil.copytree(self.source, self.target)
        for extra in ("Extra.txt", "empty-folder", "Firmware/unrelated.txt"):
            with self.subTest(extra=extra):
                (self.target / "Extra.txt").unlink(missing_ok=True)
                path = self.target / extra
                if extra == "empty-folder":
                    path.mkdir()
                else:
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(b"keep these exact bytes\0\xff")
                before = folder_bytes(self.target)
                entries = sorted(str(p.relative_to(self.target)) for p in self.target.rglob("*"))
                result = self.run_script(XCODE, "--from", self.source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("non-image folder", result.stderr)
                self.assertEqual(folder_bytes(self.target), before)
                self.assertEqual(sorted(str(p.relative_to(self.target)) for p in self.target.rglob("*")), entries)
                self.assertEqual(list(self.target.parent.glob(".ddi-*")), [])
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
                if (self.target / "Firmware").exists():
                    (self.target / "Firmware").rmdir()

    def test_unmarked_image_targets_can_be_replaced(self):
        for apple_paths in (False, True):
            with self.subTest(apple_paths=apple_paths):
                if apple_paths:
                    self.xcode_fixture()
                self.assertEqual(self.run_script(XCODE, "--from", self.source).returncode, 0)
                (self.target / ".rcpair-source").unlink()
                result = self.run_script(XCODE, "--from", self.source)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.target / ".rcpair-source").is_file())

    def test_replacement_refuses_unsafe_manifest_paths(self):
        shutil.copytree(self.source, self.target)
        (self.target / "Extra.txt").unlink()
        bad = plistlib.loads(self.expected["BuildManifest.plist"])
        for path in ("../outside.dmg", "/absolute.dmg", "Firmware/../Image.dmg"):
            bad["BuildIdentities"][1]["Manifest"][KEYS[0]]["Info"]["Path"] = path
            (self.target / "BuildManifest.plist").write_bytes(plistlib.dumps(bad))
            before = folder_bytes(self.target)
            result = self.run_script(XCODE, "--from", self.source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe payload path", result.stderr)
            self.assertEqual(folder_bytes(self.target), before)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_targets_inside_this_repository_are_refused(self):
        alias = self.root / "checkout-alias"
        alias.symlink_to(self.checkout, target_is_directory=True)
        before = folder_bytes(self.checkout)
        for target in (self.checkout / "images" / "ddi", alias / "images" / "ddi", self.checkout):
            for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
                with self.subTest(script=script.name, target=target):
                    result = self.run_script(script, *args, "--dir", target)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("image files must not live in the repository", result.stderr)
                    self.assertNotIn("Pulling", result.stderr)
                    self.assertEqual(folder_bytes(self.checkout), before)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_source_inside_this_repository_is_refused(self):
        source = self.checkout / "Restore"
        shutil.copytree(self.source, source)
        alias = self.root / "source-alias"
        alias.symlink_to(source, target_is_directory=True)
        before = folder_bytes(source)
        for path in (source, alias):
            result = self.run_script(XCODE, "--from", path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("image files must not live in the repository", result.stderr)
            self.assertEqual(folder_bytes(source), before)
            self.assertFalse(self.target.exists())

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_targets_in_other_git_work_trees_warn_and_proceed(self):
        sha = self.fixture_repo()
        other = self.root / "other-repo"
        self.git("init", "--quiet", other)
        for script, args in ((XCODE, ("--from", self.source)), (GITHUB, ())):
            target = other / script.stem / "images"
            result = self.run_script(script, *args, "--dir", target)
            self.assert_ready(result, target, "xcode" if script == XCODE else "github-pinned",
                              None if script == XCODE else sha)
            self.assertIn(f"warning: image folder is inside another git work tree: {target}", result.stderr)
            self.assertEqual(result.stderr.count("another git work tree"), 1)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_source_in_another_git_work_tree_warns_and_proceeds(self):
        other = self.root / "other-repo"
        self.git("init", "--quiet", other)
        source = other / "Restore"
        shutil.copytree(self.source, source)
        result = self.run_script(XCODE, "--from", source)
        self.assert_ready(result, self.target)
        self.assertEqual(result.stderr, f"warning: image folder is inside another git work tree: {source}\n")

    def test_tarball_checkout_skips_repository_check(self):
        if (self.checkout / ".git").exists():
            shutil.rmtree(self.checkout / ".git")
        source = self.checkout / "Restore"
        shutil.copytree(self.source, source)
        target = self.checkout / "images"
        result = self.run_script(XCODE, "--from", source, "--dir", target)
        self.assert_ready(result, target)
        self.assertEqual(result.stderr, "")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_gitignore_covers_image_files(self):
        paths = ["Image.dmg", "Image.dmg.signature", "Image.dmg.trustcache", "Image.dmg.other",
                 "Firmware/x.trustcache", "Firmware/x.cryptex_info", "Firmware/x.root_hash",
                 "BuildManifest.plist", "Restore/extra", ".rcpair/extra", "ddi/extra", ".rcpair-source"]
        ignored = self.git("-C", self.checkout, "check-ignore", "--no-index", *paths)
        self.assertEqual(ignored.splitlines(), paths)

    def test_stale_staging_folders_are_removed(self):
        self.target.parent.mkdir(parents=True)
        stale_time = time.time() - 601
        for name in (".ddi-new.abcd12", ".ddi-old.abcd12"):
            folder = self.target.parent / name
            folder.mkdir()
            (folder / "partial").write_bytes(b"interrupted install")
            os.utime(folder, (stale_time, stale_time))
        keep = []
        for name in (".ddi-new.recent", ".ddi-old.recent", "unrelated"):
            folder = self.target.parent / name
            folder.mkdir()
            (folder / "keep").write_bytes(b"keep me")
            keep.append(folder)
        os.utime(keep[-1], (stale_time, stale_time))
        symlink = self.target.parent / ".ddi-new.symlink"
        symlink.symlink_to(keep[-1], target_is_directory=True)
        os.utime(symlink, (stale_time, stale_time), follow_symlinks=False)
        result = self.run_script(XCODE, "--from", self.source)
        self.assert_ready(result, self.target)
        for name in (".ddi-new.abcd12", ".ddi-old.abcd12"):
            self.assertFalse((self.target.parent / name).exists())
        for folder in keep:
            self.assertEqual(folder_bytes(folder), {"keep": b"keep me"})
        self.assertTrue(symlink.is_symlink())

    def test_stale_cleanup_runs_before_validation(self):
        self.target.parent.mkdir(parents=True)
        stale = self.target.parent / ".ddi-new.abcd12"
        stale.mkdir()
        old_time = time.time() - 601
        os.utime(stale, (old_time, old_time))
        (self.source / "BuildManifest.plist").unlink()
        result = self.run_script(XCODE, "--from", self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(stale.exists())
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
