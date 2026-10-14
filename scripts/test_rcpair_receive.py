#!/usr/bin/env python3
"""Tests for scripts/rcpair-receive.py and the wire format v1 known-answer vector.

Run: python3 scripts/test_rcpair_receive.py          (add --write-kat to regenerate the vector file)

The receiver runs as a real subprocess on 127.0.0.1 using its test-only loopback flag. The test client
encrypts the way the iPhone app does (X25519, HKDF-SHA256, ChaCha20-Poly1305), written here on its own so
it checks the receiver and not the receiver's own helpers. No QR tool, no network, no secrets needed.
"""
import base64
import http.client
import importlib.util
import json
import os
import plistlib
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HERE = os.path.dirname(os.path.abspath(__file__))
RECEIVER = os.path.join(HERE, "rcpair-receive.py")
KAT_PATH = os.path.join(HERE, "testdata", "rcpair-send-v1-kat.json")
PATH = "/rcpair/v1/receive"


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64u_dec(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_pub(priv):
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ----------------------------------------------------------- the app side, as the spec describes it

def app_seal(recv_pub, token_raw, plaintext, eph_priv=None, nonce=None, aad=None):
    """Return the request body dict exactly as the app builds it."""
    eph_priv = eph_priv or X25519PrivateKey.generate()
    nonce = nonce or secrets.token_bytes(12)
    epk = raw_pub(eph_priv)
    shared = eph_priv.exchange(X25519PublicKey.from_public_bytes(recv_pub))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + recv_pub, info=b"rcpair send v1").derive(shared)
    ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext, token_raw if aad is None else aad)
    return {"v": 1, "t": b64u(token_raw), "epk": b64u(epk), "nonce": b64u(nonce), "ct": b64u(ct)}


def fake_record(identifier="00000000-0000-0000-0000-000000000001"):
    return plistlib.dumps({
        "public_key": bytes([0x11]) * 32,
        "private_key": bytes([0x22]) * 32,
        "identifier": identifier,
    })


def payload(udid="00008110-001A2B3C4D5E6F78", record=None, **over):
    obj = {
        "record_b64": base64.b64encode(record if record is not None else fake_record()).decode(),
        "peer": {"name": "Test iPhone", "model": "iPhone15,2", "udid": udid},
        "consent": {"version": "v1", "owner_attested": True, "adult_attested": True,
                    "accepted_at": "2026-10-13T12:00:00Z", "text_sha256": "0" * 64, "app_version": "1.0 (1)"},
        "app_version": "1.0 (1)",
    }
    obj.update(over)
    return json.dumps(obj, separators=(",", ":")).encode()


# ----------------------------------------------------------- running the receiver

class Receiver:
    def __init__(self, test, *extra, minutes="2", out=None, name=None, grace="2",
                 host="127.0.0.1", bind="127.0.0.1"):
        self.out = out or tempfile.mkdtemp(prefix="rcpair-test-")
        self.port = free_port()
        self.connect_host = host
        cmd = [sys.executable, "-u", RECEIVER, "--allow-loopback-for-tests",
               "--host", host, "--port", str(self.port), "--out", self.out,
               "--minutes", str(minutes), "--expired-grace-seconds", str(grace)]
        if bind is not None:
            cmd += ["--bind", bind]
        if name is not None:
            cmd += ["--name", name]
        cmd += list(extra)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.lines = []
        self._link_seen = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        test.addCleanup(shutil.rmtree, self.out, True)
        test.addCleanup(self.close)
        if not self._link_seen.wait(10):
            raise AssertionError("receiver never printed a link:\n" + "".join(self.lines))
        self.link = next(l for l in self.lines if l.startswith("rcpair://send?")).strip()
        q = parse_qs(urlsplit(self.link).query)
        self.query = {k: v[0] for k, v in q.items()}
        self.recv_pub = b64u_dec(self.query["k"])
        self.token_raw = b64u_dec(self.query["t"])

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line)
            if line.startswith("rcpair://send?"):
                self._link_seen.set()

    def output(self):
        return "".join(self.lines)

    def post(self, body, path=PATH, method="POST", headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn = http.client.HTTPConnection(self.connect_host, self.port, timeout=10)
        try:
            h = {"Content-Type": "application/json"}
            h.update(headers or {})
            conn.request(method, path, body=raw if method != "GET" else None, headers=h)
            resp = conn.getresponse()
            data = resp.read()
            try:
                parsed = json.loads(data)
            except ValueError:
                parsed = None
            return resp.status, parsed
        finally:
            conn.close()

    def seal(self, plaintext=None, **kw):
        return app_seal(self.recv_pub, self.token_raw, plaintext if plaintext is not None else payload(), **kw)

    def wait(self, timeout=10):
        rc = self.proc.wait(timeout=timeout)
        self._reader.join(3)
        return rc

    def files(self):
        return sorted(os.listdir(self.out))

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        if self.proc.stdout:
            self.proc.stdout.close()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ----------------------------------------------------------- the known-answer vector

def build_kat():
    recv_priv = bytes(range(1, 33))
    eph_priv = bytes(range(33, 65))
    token = bytes(range(0x80, 0x90))
    nonce = bytes(range(0xA0, 0xAC))
    record = plistlib.dumps({
        "public_key": bytes([0x11]) * 32,
        "private_key": bytes([0x22]) * 32,
        "identifier": "6F9619FF-8B86-D011-B42D-00C04FC964FF",
    })
    plaintext = json.dumps({
        "record_b64": base64.b64encode(record).decode(),
        "peer": {"name": "Test iPhone", "model": "iPhone15,2", "udid": "00008110-001A2B3C4D5E6F78"},
        "consent": {"version": "v1", "owner_attested": True, "adult_attested": True,
                    "accepted_at": "2026-10-13T12:00:00Z", "text_sha256": "ab" * 32, "app_version": "1.0 (1)"},
        "app_version": "1.0 (1)",
    }, separators=(",", ":"))
    return compute_kat(recv_priv, eph_priv, token, nonce, plaintext)


def compute_kat(recv_priv_b, eph_priv_b, token, nonce, plaintext):
    recv_priv = X25519PrivateKey.from_private_bytes(recv_priv_b)
    eph_priv = X25519PrivateKey.from_private_bytes(eph_priv_b)
    recv_pub, epk = raw_pub(recv_priv), raw_pub(eph_priv)
    shared = eph_priv.exchange(X25519PublicKey.from_public_bytes(recv_pub))
    salt = epk + recv_pub
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"rcpair send v1").derive(shared)
    sealed = ChaCha20Poly1305(key).encrypt(nonce, plaintext.encode("utf-8"), token)
    body = {"v": 1, "t": b64u(token), "epk": b64u(epk), "nonce": b64u(nonce), "ct": b64u(sealed)}
    return {
        "format": "rcpair send v1",
        "about": "Fixed inputs and every intermediate value of one send. Reproduce each 'derived' field from "
                 "'inputs' to check an implementation. Never use these keys for anything real.",
        "inputs": {
            "recv_priv_hex": recv_priv_b.hex(),
            "eph_priv_hex": eph_priv_b.hex(),
            "token_hex": token.hex(),
            "nonce_hex": nonce.hex(),
            "plaintext_utf8": plaintext,
        },
        "derived": {
            "recv_pub_hex": recv_pub.hex(),
            "recv_pub_b64url": b64u(recv_pub),
            "epk_hex": epk.hex(),
            "epk_b64url": b64u(epk),
            "token_b64url": b64u(token),
            "nonce_b64url": b64u(nonce),
            "shared_secret_hex": shared.hex(),
            "hkdf_salt_hex": salt.hex(),
            "hkdf_info_ascii": "rcpair send v1",
            "key_hex": key.hex(),
            "aad_hex": token.hex(),
            "ciphertext_hex": sealed[:-16].hex(),
            "tag_hex": sealed[-16:].hex(),
            "ct_b64url": b64u(sealed),
        },
        "link": "rcpair://send?v=1&h=192.168.1.20&p=47820&k=%s&t=%s&n=Studio%%20PC" % (b64u(recv_pub), b64u(token)),
        "request_body": body,
    }


def load_receiver_module():
    spec = importlib.util.spec_from_file_location("rcpair_receive_under_test", RECEIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------- tests

class KnownAnswer(unittest.TestCase):
    def test_kat_file_matches_a_fresh_computation_from_its_own_inputs(self):
        with open(KAT_PATH) as fh:
            kat = json.load(fh)
        i = kat["inputs"]
        again = compute_kat(bytes.fromhex(i["recv_priv_hex"]), bytes.fromhex(i["eph_priv_hex"]),
                            bytes.fromhex(i["token_hex"]), bytes.fromhex(i["nonce_hex"]), i["plaintext_utf8"])
        self.assertEqual(kat, again)

    def test_receiver_decrypts_the_kat_and_accepts_its_payload(self):
        with open(KAT_PATH) as fh:
            kat = json.load(fh)
        mod = load_receiver_module()
        priv = X25519PrivateKey.from_private_bytes(bytes.fromhex(kat["inputs"]["recv_priv_hex"]))
        plain = mod.decrypt_body(priv, bytes.fromhex(kat["inputs"]["token_hex"]), kat["request_body"])
        self.assertEqual(plain.decode(), kat["inputs"]["plaintext_utf8"])
        fields = mod.validate_payload(plain)
        self.assertEqual(fields["peer"]["udid"], "00008110-001A2B3C4D5E6F78")

    def test_kat_link_is_well_formed(self):
        with open(KAT_PATH) as fh:
            kat = json.load(fh)
        q = parse_qs(urlsplit(kat["link"]).query)
        self.assertEqual(q["v"], ["1"])
        self.assertEqual(len(b64u_dec(q["k"][0])), 32)
        self.assertEqual(len(b64u_dec(q["t"][0])), 16)
        self.assertEqual(len(q["k"][0]), 43)
        self.assertEqual(len(q["t"][0]), 22)


class ReceiverTests(unittest.TestCase):
    def test_success_writes_files_and_exits_zero(self):
        r = Receiver(self, name="Studio PC")
        out_mode = mode(r.out)
        status, body = r.post(r.seal())
        self.assertEqual((status, body), (200, {"ok": True, "name": "Studio PC"}))
        self.assertEqual(r.wait(), 0)
        udid = "00008110-001A2B3C4D5E6F78"
        self.assertEqual(r.files(), [udid + ".json", udid + ".rppair.plist"])
        plist_path = os.path.join(r.out, udid + ".rppair.plist")
        json_path = os.path.join(r.out, udid + ".json")
        self.assertEqual(mode(plist_path), 0o600)
        self.assertEqual(mode(json_path), 0o600)
        self.assertEqual(out_mode, 0o700)
        self.assertEqual(read_bytes(plist_path), fake_record())
        meta = read_json(json_path)
        self.assertEqual(sorted(meta), ["app_version", "consent", "peer", "received_at", "source_ip"])
        self.assertEqual(meta["peer"], {"name": "Test iPhone", "model": "iPhone15,2", "udid": udid})
        self.assertEqual(meta["source_ip"], "127.0.0.1")
        self.assertEqual(meta["app_version"], "1.0 (1)")
        self.assertTrue(meta["consent"]["owner_attested"])
        out = r.output()
        self.assertIn("The device is at 127.0.0.1.", out)
        self.assertIn("Your own tools can now use that pairing file", out)
        # Nothing secret is printed after the link line.
        after = out.split(r.link, 1)[1]
        self.assertNotIn(r.query["t"], after)
        self.assertNotIn(r.query["k"], after)
        self.assertNotIn(base64.b64encode(fake_record()).decode(), out)
        self.assertNotIn("private_key", out)

    def test_output_dir_is_created_0700_and_nested(self):
        base = tempfile.mkdtemp(prefix="rcpair-test-")
        self.addCleanup(shutil.rmtree, base, True)
        target = os.path.join(base, "a", "b")
        r = Receiver(self, out=target)
        self.assertEqual(mode(target), 0o700)
        r.close()

    def test_wrong_token_is_401_and_does_not_burn_the_real_one(self):
        r = Receiver(self)
        bad = r.seal()
        bad["t"] = b64u(secrets.token_bytes(16))
        self.assertEqual(r.post(bad), (401, {"error": "token"}))
        self.assertEqual(r.files(), [])
        self.assertEqual(r.post(r.seal())[0], 200)
        self.assertEqual(r.wait(), 0)

    def test_tampered_ciphertext_is_400(self):
        r = Receiver(self)
        body = r.seal()
        ct = bytearray(b64u_dec(body["ct"]))
        ct[3] ^= 1
        body["ct"] = b64u(bytes(ct))
        self.assertEqual(r.post(body), (400, {"error": "bad request"}))
        tag = bytearray(b64u_dec(r.seal()["ct"]))
        tag[-1] ^= 1
        body2 = r.seal()
        body2["ct"] = b64u(bytes(tag))
        self.assertEqual(r.post(body2)[0], 400)
        self.assertEqual(r.files(), [])
        self.assertEqual(r.post(r.seal())[0], 200)

    def test_other_bad_requests_are_400(self):
        r = Receiver(self)
        self.assertEqual(r.post(b"not json")[0], 400)
        self.assertEqual(r.post(b"[1,2]")[0], 400)
        self.assertEqual(r.post({"v": 2, "t": b64u(r.token_raw)})[0], 400)
        good = r.seal()
        for field, value in (("epk", "!!!"), ("nonce", b64u(b"short")), ("ct", "AA"), ("epk", b64u(bytes(32)))):
            body = dict(good)
            body[field] = value
            self.assertEqual(r.post(body)[0], 400, field)
        # Sealed to a different aad (token) must not open.
        self.assertEqual(r.post(r.seal(aad=b"x" * 16))[0], 400)
        # Sealed to a different receiver key.
        other = app_seal(raw_pub(X25519PrivateKey.generate()), r.token_raw, payload())
        self.assertEqual(r.post(other)[0], 400)
        self.assertEqual(r.files(), [])
        self.assertEqual(r.post(r.seal())[0], 200)

    def test_decrypted_but_not_a_pairing_record_is_400(self):
        r = Receiver(self)
        cases = {
            "garbage plist": payload(record=b"hello"),
            "missing public_key": payload(record=plistlib.dumps({"private_key": bytes(32), "identifier": "x"})),
            "short private_key": payload(record=plistlib.dumps(
                {"public_key": bytes(32), "private_key": bytes(31), "identifier": "x"})),
            "key as string": payload(record=plistlib.dumps(
                {"public_key": "a" * 32, "private_key": bytes(32), "identifier": "x"})),
            "identifier not str": payload(record=plistlib.dumps(
                {"public_key": bytes(32), "private_key": bytes(32), "identifier": 5})),
            "plist over 8192": payload(record=plistlib.dumps(
                {"public_key": bytes(32), "private_key": bytes(32), "identifier": "x", "pad": "p" * 9000})),
            "udid traversal": payload(udid="../../etc/x"),
            "udid empty": payload(udid=""),
            "bad base64": json.dumps({"record_b64": "***", "peer": {"udid": "a"}, "consent": {},
                                      "app_version": "1"}).encode(),
            "no consent": json.dumps({"record_b64": base64.b64encode(fake_record()).decode(),
                                      "peer": {"name": "n", "model": "m", "udid": "a"},
                                      "app_version": "1"}).encode(),
            "not an object": b"[]",
        }
        for label, plain in cases.items():
            self.assertEqual(r.post(r.seal(plain))[0], 400, label)
        self.assertEqual(r.files(), [])
        self.assertEqual(r.post(r.seal())[0], 200)

    def test_second_upload_is_refused_and_first_record_is_kept(self):
        r = Receiver(self)
        self.assertEqual(r.post(r.seal())[0], 200)
        try:
            status, body = r.post(r.seal(payload(record=fake_record("SECOND-RECORD"))))
            self.assertEqual((status, body), (401, {"error": "token"}))
        except (ConnectionError, OSError):
            pass  # the receiver already exited, which is also a refusal
        self.assertEqual(r.wait(), 0)
        plist_path = os.path.join(r.out, "00008110-001A2B3C4D5E6F78.rppair.plist")
        self.assertEqual(read_bytes(plist_path), fake_record())

    def test_two_uploads_at_once_save_exactly_one(self):
        r = Receiver(self)
        results = []

        def go(tag):
            try:
                results.append(r.post(r.seal(payload(record=fake_record(tag)))))
            except OSError:
                results.append((0, None))

        threads = [threading.Thread(target=go, args=("rec%d" % i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for s, _ in results if s == 200), 1, results)
        self.assertEqual(r.wait(), 0)
        self.assertEqual(len(r.files()), 2)

    def test_oversize_body_is_413(self):
        r = Receiver(self)
        status, body = r.post(b"x" * (64 * 1024 + 1))
        self.assertEqual((status, body), (413, {"error": "too large"}))
        status, _ = r.post(b"x" * 200000)
        self.assertEqual(status, 413)
        self.assertEqual(r.files(), [])
        # Exactly at the limit is read and judged on its content, not size.
        self.assertEqual(r.post(b" " * (64 * 1024))[0], 400)
        self.assertEqual(r.post(r.seal())[0], 200)

    def test_expired_window_answers_410_then_exits_1(self):
        r = Receiver(self, minutes=0.02, grace=4)   # 1.2 seconds
        time.sleep(1.6)
        status, body = r.post(r.seal())
        self.assertEqual((status, body), (410, {"error": "expired"}))
        self.assertEqual(r.wait(), 1)
        self.assertEqual(r.files(), [])
        self.assertIn("window ended", r.output())

    def test_twenty_wrong_tokens_stop_the_receiver(self):
        r = Receiver(self)
        body = r.seal()
        body["t"] = b64u(secrets.token_bytes(16))
        for _ in range(19):
            self.assertEqual(r.post(body)[0], 401)
        # The real token is still good on attempt 20.
        self.assertEqual(r.post(r.seal())[0], 200)
        self.assertEqual(r.wait(), 0)

        r2 = Receiver(self)
        body = r2.seal()
        body["t"] = b64u(secrets.token_bytes(16))
        for _ in range(20):
            try:
                self.assertEqual(r2.post(body)[0], 401)
            except OSError:
                break
        self.assertEqual(r2.wait(), 1)
        self.assertEqual(r2.files(), [])
        self.assertIn("Too many wrong tokens", r2.output())

    def test_other_paths_and_methods_are_404(self):
        r = Receiver(self)
        self.assertEqual(r.post(r.seal(), path="/")[0], 404)
        self.assertEqual(r.post(r.seal(), path=PATH + "/x")[0], 404)
        self.assertEqual(r.post(b"", method="GET")[0], 404)
        self.assertEqual(r.post(r.seal())[0], 200)

    def test_on_receive_hook_gets_both_paths(self):
        d = tempfile.mkdtemp(prefix="rcpair-hook-")
        hook = os.path.join(d, "hook.py")
        mark = os.path.join(d, "args.json")
        with open(hook, "w") as fh:
            fh.write("import json, sys\njson.dump(sys.argv[1:], open(%r, 'w'))\n" % mark)
        r = Receiver(self, "--on-receive", "%s %s extra" % (sys.executable, hook))
        self.assertEqual(r.post(r.seal())[0], 200)
        self.assertEqual(r.wait(), 0)
        args = read_json(mark)
        self.assertEqual(args[0], "extra")
        self.assertTrue(args[1].endswith(".rppair.plist"))
        self.assertTrue(args[2].endswith(".json"))
        self.assertTrue(all(os.path.exists(a) for a in args[1:]))

    def test_name_is_capped_at_40_and_cleaned(self):
        r = Receiver(self, name="Studio\x1b[31m PC " + "x" * 80)
        n = unquote(r.query["n"])
        self.assertLessEqual(len(n), 40)
        self.assertNotIn("\x1b", n)
        self.assertTrue(n.startswith("Studio[31m PC"))
        self.assertEqual(r.query["v"], "1")
        self.assertEqual(len(r.query["k"]), 43)
        self.assertEqual(len(r.query["t"]), 22)
        self.assertEqual(r.query["h"], "127.0.0.1")
        self.assertEqual(int(r.query["p"]), r.port)

    def test_link_is_the_only_place_the_token_appears(self):
        r = Receiver(self)
        occurrences = [l for l in r.lines if r.query["t"] in l]
        self.assertEqual(len(occurrences), 1)

    def test_each_run_makes_a_new_key_and_token(self):
        a, b = Receiver(self), Receiver(self)
        self.assertNotEqual(a.query["k"], b.query["k"])
        self.assertNotEqual(a.query["t"], b.query["t"])

    def test_refuses_public_or_loopback_host_without_the_test_flag(self):
        for host in ("8.8.8.8", "127.0.0.1", "203.0.113.5"):
            p = subprocess.run([sys.executable, RECEIVER, "--host", host, "--port", str(free_port()),
                                "--out", tempfile.mkdtemp(prefix="rcpair-test-")],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 2, host)
            self.assertIn("not a private address", p.stderr)
            self.assertNotIn("rcpair://", p.stdout)

    def test_deeply_nested_json_is_400_and_prints_no_traceback(self):
        r = Receiver(self)
        for blob in (b"[" * 60000, b'{"v":1,"t":"' + b"x" * 22 + b'","a":' + b"[" * 30000):
            self.assertEqual(r.post(blob)[0], 400)
        # Same trap inside the decrypted plaintext.
        self.assertEqual(r.post(r.seal(b"[" * 30000))[0], 400)
        self.assertEqual(r.post(r.seal())[0], 200)
        self.assertEqual(r.wait(), 0)
        self.assertNotIn("Traceback", r.output())
        self.assertNotIn("Exception occurred", r.output())

    def test_ipv6_host_without_bind_listens_on_ipv6(self):
        if not socket.has_ipv6:
            self.skipTest("no IPv6 on this machine")
        try:
            probe = socket.socket(socket.AF_INET6)
            probe.bind(("::1", 0))
            probe.close()
        except OSError:
            self.skipTest("no IPv6 loopback on this machine")
        r = Receiver(self, host="::1", bind=None)
        self.assertEqual(r.query["h"], "::1")
        self.assertEqual(r.post(r.seal())[0], 200)
        self.assertEqual(r.wait(), 0)

    def test_ipv6_host_with_ipv4_bind_is_refused(self):
        p = subprocess.run([sys.executable, RECEIVER, "--allow-loopback-for-tests", "--host", "::1",
                            "--bind", "0.0.0.0", "--port", str(free_port()),
                            "--out", tempfile.mkdtemp(prefix="rcpair-test-")],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--bind ::", p.stderr)
        self.assertNotIn("rcpair://", p.stdout)

    def test_link_local_ipv6_host_is_refused(self):
        for host in ("fe80::1%eth0", "fe80::1", "fe80::1%eth0.100"):
            p = subprocess.run([sys.executable, RECEIVER, "--host", host, "--port", str(free_port()),
                                "--out", tempfile.mkdtemp(prefix="rcpair-test-")],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 2, host)
            self.assertIn("link-local", p.stderr)
            self.assertNotIn("rcpair://", p.stdout)

    def test_host_names_the_app_would_refuse_are_refused_here(self):
        for host in ("my_mac.lan", "mac.local.", "-bad.lan", "a..b", "x" * 64 + ".lan"):
            p = subprocess.run([sys.executable, RECEIVER, "--host=" + host, "--port", str(free_port()),
                                "--out", tempfile.mkdtemp(prefix="rcpair-test-")],
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 2, host)
            self.assertIn("not a name the phone accepts", p.stderr)
            self.assertNotIn("rcpair://", p.stdout)

    def test_good_host_names_are_accepted(self):
        for host in ("mac.local", "studio-pc", "Studio-PC.lan"):
            r = Receiver(self, host=host, bind="127.0.0.1")
            self.assertEqual(r.query["h"], host)
            r.proc.terminate()

    def test_bad_port_is_refused(self):
        p = subprocess.run([sys.executable, RECEIVER, "--host", "10.0.0.5", "--port", "80"],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--port", p.stderr)


class SessionScript(unittest.TestCase):
    """rcpair-session.sh cannot pass launch environment with the pinned idevice tools, and must say so."""

    def run_script(self, *args):
        return subprocess.run(["bash", os.path.join(HERE, "rcpair-session.sh"), *args],
                              capture_output=True, text=True, timeout=15)

    def test_valid_use_exits_3_and_explains(self):
        for args in (("start",), ("end", "--name", "Studio PC"), ("start", "--name=x")):
            p = self.run_script(*args)
            self.assertEqual(p.returncode, 3, args)
            self.assertIn("cannot pass environment variables", p.stderr)
            self.assertIn("nothing was launched", p.stderr)

    def test_bad_use_exits_2(self):
        for args in ((), ("begin",), ("start", "end"), ("start", "--name"), ("start", "--bogus")):
            self.assertEqual(self.run_script(*args).returncode, 2, args)

    def test_help_exits_0_and_states_the_limit(self):
        p = self.run_script("--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("v0.1.68", p.stdout)
        self.assertIn("empty environment", p.stdout)


def write_kat():
    os.makedirs(os.path.dirname(KAT_PATH), exist_ok=True)
    with open(KAT_PATH, "w") as fh:
        json.dump(build_kat(), fh, indent=2)
        fh.write("\n")
    print("wrote", KAT_PATH)


if __name__ == "__main__":
    if "--write-kat" in sys.argv:
        sys.argv.remove("--write-kat")
        write_kat()
    unittest.main(verbosity=2)
