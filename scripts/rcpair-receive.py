#!/usr/bin/env python3
"""rcpair-receive: receive one pairing from the RC Pair app, on your own computer.

Run it, scan the QR code it shows with the Camera app on the phone (or copy the link into the app's
Paste link button), and confirm on the phone. The app encrypts the pairing record to a key made for this
run, so only this process can read it. The record goes to this computer and nowhere else. This script
makes no outbound connection.

It accepts exactly one successful upload, writes <udid>.rppair.plist and <udid>.json into --out, and
exits. It speaks wire format v1.

Needs Python 3.9+ and the 'cryptography' package. For a QR code in the terminal it uses the 'qrcode'
Python package or the 'qrencode' command if either is installed. Without them it prints the link only.

Exit codes: 0 received, 1 window ended or too many wrong tokens, 2 bad options or cannot start.
"""
import argparse
import base64
import binascii
import hmac
import ipaddress
import json
import os
import plistlib
import re
import secrets
import shlex
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:  # pragma: no cover
    sys.stderr.write("rcpair-receive needs the Python 'cryptography' package. Run pip install cryptography and try again.\n")
    sys.exit(2)

PATH = "/rcpair/v1/receive"
MAX_BODY = 64 * 1024
MAX_PLIST = 8192
MAX_WRONG_TOKENS = 20
HKDF_INFO = b"rcpair send v1"
UDID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")

# Private networks the app will send to (see the address rule in the wire format).
PRIVATE_V4 = [ipaddress.ip_network(n) for n in
              ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16")]
PRIVATE_V6 = [ipaddress.ip_network(n) for n in ("fc00::/7", "fe80::/10")]
# Same rule the app applies to a name: letters, digits and hyphens, no empty label.
HOSTNAME_RE = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*")
CGNAT = ipaddress.ip_network("100.64.0.0/10")


# ---------------------------------------------------------------- encoding helpers

def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(text, expect_len=None):
    """Strict base64url without padding. Returns bytes or raises ValueError."""
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    if len(text) % 4 == 1:
        raise ValueError("bad length")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if expect_len is not None and len(raw) != expect_len:
        raise ValueError("wrong size")
    return raw


def clean_text(text, limit):
    """Drop control characters (so a name cannot move the cursor or paint the screen) and cap length."""
    out = "".join(ch for ch in str(text) if unicodedata.category(ch)[0] != "C")
    return out[:limit]


def is_private_ip(ip) -> bool:
    nets = PRIVATE_V4 if ip.version == 4 else PRIVATE_V6
    return any(ip in n for n in nets)


# ---------------------------------------------------------------- crypto (wire format v1, section 1)

def public_raw(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def derive_key(shared: bytes, epk_raw: bytes, recv_pub_raw: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=epk_raw + recv_pub_raw, info=HKDF_INFO).derive(shared)


def decrypt_body(recv_priv: X25519PrivateKey, token_raw: bytes, body: dict) -> bytes:
    """Decrypt a v1 request body. Raises ValueError on anything wrong (the caller answers 400)."""
    try:
        epk = b64u_decode(body.get("epk"), 32)
        nonce = b64u_decode(body.get("nonce"), 12)
        ct = b64u_decode(body.get("ct"))
        if len(ct) < 16:
            raise ValueError("short ct")
        shared = recv_priv.exchange(X25519PublicKey.from_public_bytes(epk))
        key = derive_key(shared, epk, public_raw(recv_priv))
        return ChaCha20Poly1305(key).decrypt(nonce, ct, token_raw)
    except ValueError:
        raise
    except Exception as exc:  # InvalidTag and friends
        raise ValueError("decrypt failed") from exc


def validate_payload(plain: bytes) -> dict:
    """Check the decrypted JSON. Returns the cleaned fields or raises ValueError."""
    try:
        data = json.loads(plain.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        record = base64.b64decode(data.get("record_b64", ""), validate=True)
    except (UnicodeDecodeError, json.JSONDecodeError, binascii.Error, TypeError, AttributeError,
            RecursionError) as exc:
        raise ValueError("bad payload") from exc
    if not record or len(record) > MAX_PLIST:
        raise ValueError("bad record size")
    try:
        rec = plistlib.loads(record)
    except Exception as exc:
        raise ValueError("not a plist") from exc
    if not isinstance(rec, dict):
        raise ValueError("not a dict")
    for k in ("public_key", "private_key"):
        if not isinstance(rec.get(k), bytes) or len(rec[k]) != 32:
            raise ValueError("bad " + k)
    if not isinstance(rec.get("identifier"), str) or not rec["identifier"]:
        raise ValueError("bad identifier")
    peer = data.get("peer")
    if not isinstance(peer, dict):
        raise ValueError("no peer")
    udid = peer.get("udid")
    if not isinstance(udid, str) or not UDID_RE.fullmatch(udid):
        raise ValueError("bad udid")
    consent = data.get("consent")
    if not isinstance(consent, dict):
        raise ValueError("no consent")
    app_version = data.get("app_version")
    if not isinstance(app_version, str):
        raise ValueError("no app_version")
    return {
        "record": record,
        "peer": {
            "name": clean_text(peer.get("name", ""), 80),
            "model": clean_text(peer.get("model", ""), 80),
            "udid": udid,
        },
        "consent": consent,
        "app_version": clean_text(app_version, 64),
    }


# ---------------------------------------------------------------- state and HTTP

class State:
    def __init__(self, recv_priv, token_raw, out_dir, name, deadline, allow_loopback=False):
        self.recv_priv = recv_priv
        self.token_raw = token_raw
        self.token_str = b64u(token_raw)
        self.out_dir = out_dir
        self.name = name
        self.deadline = deadline
        self.lock = threading.Lock()
        self.used = False
        self.wrong = 0
        self.result = None          # dict once a record was saved
        self.finished = threading.Event()
        self.give_up = False        # too many wrong tokens

    def expired(self):
        return time.monotonic() >= self.deadline


def write_private(path, data: bytes):
    """Write a 0600 file atomically."""
    tmp = "%s.tmp%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_record(state: State, fields: dict, source_ip: str):
    udid = fields["peer"]["udid"]
    plist_path = os.path.join(state.out_dir, udid + ".rppair.plist")
    json_path = os.path.join(state.out_dir, udid + ".json")
    write_private(plist_path, fields["record"])
    meta = {
        "peer": fields["peer"],
        "consent": fields["consent"],
        "app_version": fields["app_version"],
        "source_ip": source_ip,
        "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_private(json_path, (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return plist_path, json_path, meta


class Handler(BaseHTTPRequestHandler):
    server_version = "rcpair-receive"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10

    def log_message(self, fmt, *args):  # never log request details
        pass

    def reply(self, code, obj):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self):
        self.reply(404, {"error": "not found"})

    do_PUT = do_DELETE = do_PATCH = do_GET

    def do_POST(self):
        st: State = self.server.state
        if self.path != PATH:
            return self.reply(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.reply(400, {"error": "bad request"})
        if length < 0:
            return self.reply(400, {"error": "bad request"})
        if length > MAX_BODY:
            # Read and drop a bounded amount so the sender sees our answer, not a reset.
            remaining = min(length, 4 * 1024 * 1024)
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            return self.reply(413, {"error": "too large"})
        raw = self.rfile.read(length)
        if len(raw) != length:
            return self.reply(400, {"error": "bad request"})

        with st.lock:
            if st.expired():
                return self.reply(410, {"error": "expired"})
            if st.used or st.give_up:
                return self.reply(401, {"error": "token"})
            try:
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict) or body.get("v") != 1 or not isinstance(body.get("t"), str):
                    raise ValueError("shape")
            except (UnicodeDecodeError, ValueError, RecursionError):
                return self.reply(400, {"error": "bad request"})
            if not hmac.compare_digest(body["t"].encode("utf-8", "replace"), st.token_str.encode()):
                st.wrong += 1
                if st.wrong >= MAX_WRONG_TOKENS:
                    st.give_up = True
                    st.finished.set()
                return self.reply(401, {"error": "token"})
            try:
                plain = decrypt_body(st.recv_priv, st.token_raw, body)
                fields = validate_payload(plain)
            except ValueError:
                return self.reply(400, {"error": "bad request"})
            source_ip = self.client_address[0]
            if source_ip.startswith("::ffff:"):
                source_ip = source_ip[7:]
            try:
                paths = save_record(st, fields, source_ip)
            except OSError:
                return self.reply(500, {"error": "could not save"})
            st.used = True
            st.result = {"paths": paths[:2], "meta": paths[2]}
            self.reply(200, {"ok": True, "name": st.name})
        st.finished.set()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, state):
        self.address_family = socket.AF_INET6 if ":" in addr[0] else socket.AF_INET
        self.state = state
        super().__init__(addr, Handler)

    def server_bind(self):
        # HTTPServer.server_bind also calls socket.getfqdn() on the address, a reverse DNS lookup
        # that can stall for many seconds (on macOS especially) before the QR code prints.
        # Nothing here uses server_name, so skip the lookup.
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = self.server_address[1]

    def handle_error(self, request, client_address):
        # Never print a traceback built from a stranger's request. The sender just sees the connection close.
        pass


# ---------------------------------------------------------------- addresses and QR

def local_ipv4_addresses():
    found = []
    ip_cmd = shutil.which("ip")
    if ip_cmd:
        try:
            out = subprocess.run([ip_cmd, "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/", line)
                if m:
                    found.append(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # no packet is sent
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    seen, out = set(), []
    for a in found:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def default_host():
    addrs = [ipaddress.ip_address(a) for a in local_ipv4_addresses()]
    for a in addrs:
        if a in CGNAT:
            return str(a)
    for a in addrs:
        if is_private_ip(a) and not a.is_link_local:
            return str(a)
    return None


def warn_if_name_not_private(host):
    """The app refuses a name unless every address it resolves to is private. Say so before the phone does."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        sys.stderr.write("rcpair-receive: warning, %s does not resolve on this computer. "
                         "Check the name, or pass an IP address.\n" % host)
        return
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            continue
        if not is_private_ip(ip) and not ip.is_loopback:
            sys.stderr.write("rcpair-receive: warning, %s also resolves to %s, which is not private. "
                             "The phone will refuse this name. Pass an IP address.\n" % (host, ip))
            return


def build_link(host, port, recv_pub_raw, token_raw, name):
    return "rcpair://send?v=1&h=%s&p=%d&k=%s&t=%s&n=%s" % (
        quote(host, safe=":"), port, b64u(recv_pub_raw), b64u(token_raw), quote(name, safe=""))


def print_qr(link):
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(link)
        qr.make(fit=True)
        m = qr.get_matrix()
        if len(m) % 2:
            m.append([False] * len(m[0]))
        # Black modules on a white background whatever the terminal theme, so any scanner reads it.
        glyph = {(0, 0): " ", (1, 0): "\u2580", (0, 1): "\u2584", (1, 1): "\u2588"}
        lines = ["\x1b[30;47m" + "".join(glyph[(1 if m[r][c] else 0, 1 if m[r + 1][c] else 0)]
                                        for c in range(len(m[0]))) + "\x1b[0m"
                 for r in range(0, len(m), 2)]
        print("\n".join(lines))
        return True
    except ImportError:
        pass
    except Exception:
        pass
    exe = shutil.which("qrencode")
    if exe:
        try:
            out = subprocess.run([exe, "-t", "ANSIUTF8", "-m", "2", "-l", "L", "-o", "-", link],
                                 capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                print(out.stdout.rstrip("\n"))
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


# ---------------------------------------------------------------- main

def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="rcpair-receive",
        description="Receive one RC Pair pairing from the phone, on this computer. Shows a QR code and a link, "
                    "waits for the app to send, saves the record, and exits.")
    ap.add_argument("--bind", default=None,
                    help="address to listen on (default 0.0.0.0, or :: when --host is an IPv6 address)")
    ap.add_argument("--port", type=int, default=47820, help="TCP port, 1024-65535 (default 47820)")
    ap.add_argument("--host", help="address or name the phone should use to reach this computer "
                                   "(default the first 100.64/10 address, else the first private LAN address)")
    ap.add_argument("--name", help="computer name the phone shows on its confirm sheet (default short hostname)")
    ap.add_argument("--out", default="~/.rcpair", help="folder for the saved files, created 0700 (default ~/.rcpair)")
    ap.add_argument("--minutes", type=float, default=10.0, help="how long to wait (default 10)")
    ap.add_argument("--on-receive", metavar="CMD",
                    help="command to run after the record is saved. It gets the .rppair.plist path and the .json path "
                         "as its last two arguments.")
    ap.add_argument("--allow-loopback-for-tests", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--expired-grace-seconds", type=float, default=15.0, help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def fail(msg):
    sys.stderr.write("rcpair-receive: %s\n" % msg)
    sys.exit(2)


def main(argv=None):
    args = parse_args(argv)
    test_mode = args.allow_loopback_for_tests
    if not (1024 <= args.port <= 65535):
        fail("--port must be between 1024 and 65535.")
    if not (args.minutes > 0):
        fail("--minutes must be more than 0.")

    host = args.host or default_host()
    if not host:
        fail("could not find a private address for this computer. Pass --host with the address the phone can reach.")
    try:
        lit = ipaddress.ip_address(host)
    except ValueError:
        lit = None
    if lit is not None and lit.version == 6 and (getattr(lit, "scope_id", None) or lit.is_link_local):
        fail("%s is a link-local IPv6 address, which the phone cannot use because its zone name only means "
             "something on this computer. Pass --host with a LAN IPv4, Tailscale, or fd00::/8 address." % host)
    if lit is not None and not is_private_ip(lit) and not (test_mode and lit.is_loopback):
        fail("%s is not a private address. RC Pair only sends to a computer on your own network or private network, "
             "so the phone would refuse it. Pass --host with a LAN, Tailscale, or other private address." % host)
    if lit is None:
        if len(host) > 253 or not HOSTNAME_RE.fullmatch(host):
            fail("%r is not a name the phone accepts. Use letters, digits and hyphens, with no underscore, "
                 "empty part or trailing dot, or pass an IP address." % host)
        warn_if_name_not_private(host)

    bind = args.bind
    if bind is None:
        bind = "::" if lit is not None and lit.version == 6 else "0.0.0.0"
    elif lit is not None and lit.version == 6 and ":" not in bind:
        fail("--host is IPv6 but --bind %s is IPv4, so the phone could not connect. Pass --bind :: as well." % bind)

    name = clean_text(args.name if args.name else socket.gethostname().split(".")[0], 40).strip() or "computer"

    out_dir = os.path.expanduser(args.out)
    try:
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
        os.chmod(out_dir, 0o700)
    except OSError as exc:
        fail("cannot use the output folder %s (%s)" % (out_dir, exc.strerror or exc))

    recv_priv = X25519PrivateKey.generate()
    token_raw = secrets.token_bytes(16)
    window = args.minutes * 60.0
    state = State(recv_priv, token_raw, out_dir, name, time.monotonic() + window)

    try:
        server = Server((bind, args.port), state)
    except OSError as exc:
        fail("cannot listen on %s port %d (%s)" % (bind, args.port, exc.strerror or exc))
    bound_port = server.server_address[1]

    link = build_link(host, bound_port, public_raw(recv_priv), token_raw, name)
    print("RC Pair receiver for %s" % name)
    print()
    ok = print_qr(link)
    if ok:
        print()
        print("Scan the code with the Camera app on the phone, then confirm in RC Pair.")
    else:
        print("No QR tool found (install qrencode or the Python qrcode package to see one).")
    print("Or copy this link and tap Paste link in RC Pair.")
    print()
    print(link)
    print()
    print("Waiting up to %g minute%s on %s port %d. Press Ctrl-C to cancel." % (
        args.minutes, "" if args.minutes == 1 else "s", bind, bound_port))
    sys.stdout.flush()

    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()

    code = 1
    try:
        state.finished.wait(timeout=window)
        if state.result is None and not state.give_up:
            # Window over. Keep answering for a short while so a late phone gets "expired", not silence.
            print("The window ended. Nothing was received.")
            sys.stdout.flush()
            state.finished.wait(timeout=max(0.0, args.expired_grace_seconds))
        if state.result is not None:
            code = 0
        elif state.give_up:
            print("Too many wrong tokens. Stopping.")
    except KeyboardInterrupt:
        print("Cancelled.")
    finally:
        server.shutdown()
        server.server_close()

    if code != 0:
        sys.exit(code)

    plist_path, json_path = state.result["paths"]
    meta = state.result["meta"]
    peer = meta["peer"]
    label = " ".join(x for x in (clean_text(peer["name"], 80), "(%s)" % clean_text(peer["model"], 80) if peer["model"] else "") if x)
    print("Received the pairing for %s." % (label or peer["udid"]))
    print("The device is at %s." % clean_text(meta["source_ip"], 64))
    print("Its id is %s." % peer["udid"])
    print("Saved %s" % plist_path)
    print("Saved %s" % json_path)
    print()
    print("Your own tools can now use that pairing file to reach the device at the address above.")
    sys.stdout.flush()

    if args.on_receive:
        try:
            cmd = shlex.split(args.on_receive) + [plist_path, json_path]
            rc = subprocess.run(cmd, timeout=300).returncode
            if rc != 0:
                sys.stderr.write("rcpair-receive: the --on-receive command exited with status %d.\n" % rc)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            sys.stderr.write("rcpair-receive: could not run the --on-receive command (%s).\n" % exc)
    sys.exit(0)


if __name__ == "__main__":
    main()
