# Architecture: driving an iPhone from Linux

This is how it works. The pieces are public; what is less common in the open is running
the whole thing from a **headless Linux host** over USB or the network. This document centers the
path that installs **no automation runner on the phone**.

## Two paths

There are two ways to drive the device. This project centers the first.

**No-app, direct (what this documents).** Your computer speaks Apple's own on-device developer
services over a tunnel and drives the phone directly: read the screen, tap, type, swipe, launch
apps. No automation runner is installed on the phone. The one app, RC Pair, makes the pairing record
and hands it to your computer. You turn on Developer Mode, pair, and connect the device once to Xcode
on a Mac, which puts Apple's Developer Disk Image on it. Your computer does the rest. Built on [idevice](https://github.com/jkcoxson/idevice) (MIT).

**Installed runner (optional, more structured).** A development-signed XCUITest app
([iCTRL](https://github.com/OpenMinis/iCTRL), Apache-2.0) runs on the phone and exposes a JSON-RPC
API that can read an app's accessibility tree and drive elements precisely. More capable for
element-level automation, but it is an app: you sign it with your own Apple account and re-sign a
free-account build every 7 days. Use it only if you need structured UI automation; most agent use
does not.

## The no-app path, piece by piece

```
  your computer (Linux)                 your iPhone / iPad (your Apple account)
  ---------------------                 --------------------------------------
  [ your agent / model ]                [ Developer Mode on, paired, DDI from Xcode ]
          |                                         ^
  [ control layer ] -- taps/type/launch ---------->|   Apple developer services
          |           <-- screen frames -----------
  [ idevice tunnel ] -- CoreDevice/RemotePairing -->
          |
```

0. **Developer Mode, then hand-off.** Install RC Pair on the device and turn on Developer Mode as it
   guides you (the device restarts). A pairing made while Developer Mode is off does not verify. Then
   pair in RC Pair, run `scripts/rcpair-receive.py` on your
   computer and scan its QR code with the phone's Camera app. The phone sends the pairing record to
   that computer. See "The QR hand-off" below.
1. **Pair (or, instead of step 0).** Your computer pairs with the device. On the same USB or LAN, mDNS finds it; across a
   VPN, pair at an explicit IP, because mDNS does not cross a VPN. The phone shows Trust and a
   6-digit code. That is the consent step. (idevice's `pair`.)
2. **Tunnel.** Open the CoreDevice tunnel to reach the developer services. (idevice's `pair`, with
   its tunnel option.)
3. **The DDI.** Apple's Developer Disk Image has to be on the device. Connect the device once to
   Xcode on a Mac and Xcode puts it there. In our testing it stayed on after a restart. An iOS update
   usually means doing it again.
4. **Drive.** Inject taps, drags, typing, and buttons (idevice's `hid`), launch and list apps
   (idevice's `app_service`), and read the screen from the device's own developer stream.
5. **Decide.** Point your own agent or model at that control layer. It runs on your computer too.

`scripts/connect.sh` builds these tools and prints steps 1-3. Each tool prints its exact options with
`-h`; flags can differ by version, so follow `-h` on the version you pulled.

## The QR hand-off

RC Pair on the phone makes the pairing record, and `scripts/rcpair-receive.py` on your computer
receives it. The record goes only to the computer you choose. The app sends nothing to a publisher
server, and no version of it does.

```
  your computer                              your iPhone / iPad
  -------------                              ------------------
  rcpair-receive.py  --- QR code / link --->  Camera app opens RC Pair
  (new key + token per run)                   you confirm the computer's name and address
          ^                                           |
          +---- one encrypted HTTP POST --------------+   private addresses only
```

Wire format v1, fixed so the app and the receiver match byte for byte.

Link, shown as a QR code and as text.

```
rcpair://send?v=1&h=<host>&p=<port>&k=<receiver key>&t=<token>&n=<computer name>
```

- `v` is `1`. `h` is an IPv4 or IPv6 literal (no brackets) or a hostname. `p` is the TCP port, 1024 to 65535.
- `k` is the receiver's X25519 public key, 32 bytes, base64url without padding.
- `t` is a one-time token, 16 random bytes, base64url without padding.
- `n` is the computer name, percent-encoded UTF-8, at most 40 characters. The app strips control
  characters and shows it as plain text.
- Unknown items are ignored. A missing or malformed item is rejected.

The phone resolves `h` and sends only if every resolved address is private. That means IPv4 `10/8`,
`172.16/12`, `192.168/16`, `100.64/10` and `169.254/16`, and IPv6 `fc00::/7` and `fe80::/10`. Loopback
and anything else is refused. It connects to the resolved IP address, not the name.

rcpair-receive checks its own `--host` against these rules before it prints a link. It refuses a public
address, a link-local IPv6 address (its zone name means nothing on the phone), and a name the app cannot
parse, such as one with an underscore or a trailing dot. It warns when a name also resolves to a public
address. With an IPv6 `--host` it listens on `::` unless you pass `--bind`.

Request, at most 64 KiB.

```
POST http://<ip>:<port>/rcpair/v1/receive
{"v":1,"t":"<token>","epk":"<ephemeral X25519 public>","nonce":"<12 bytes>","ct":"<ciphertext||tag>"}
```

All binary fields are base64url without padding.

- A fresh X25519 key pair is made per send. `shared` is X25519 of the ephemeral private key and `k`.
- `key` is HKDF-SHA256 with `shared` as input, salt `epk || k` (both raw 32 bytes), info the ASCII
  string `rcpair send v1`, and 32 bytes of output.
- `ct` is ChaCha20-Poly1305 of the plaintext under `key` with a random 12 byte nonce. The
  associated data is the raw 16 token bytes. `ct` is the ciphertext followed by the 16 byte tag.

The plaintext is UTF-8 JSON with these members and nothing else.

```
{"record_b64": "<the pairing record plist, standard base64>",
 "peer": {"name": "...", "model": "...", "udid": "..."},
 "consent": { ... the consent record the app made ... },
 "app_version": "<version> (<build>)"}
```

Responses.

- `200 {"ok":true,"name":"<receiver name>"}` once the record was decrypted, checked and saved.
- `401 {"error":"token"}` for a wrong or already used token.
- `400 {"error":"bad request"}` for bad JSON, bad base64, a failed decrypt, or something that is not a
  pairing record (32 byte `public_key` and `private_key`, a string `identifier`, at most 8192 bytes).
- `410 {"error":"expired"}` after the receiver's window. `413` for a body over 64 KiB.

The receiver accepts exactly one successful upload and then exits. It exits after 20 wrong tokens. It
writes `<udid>.rppair.plist` and `<udid>.json` with mode 0600 in a 0700 folder and never prints the
key or the token after startup. It makes no outbound connection.

The traffic is plain HTTP on a private network, protected by the encryption above. Anyone who sees the
QR code can send to that receiver while it is waiting, so the receiver prints the device's address
when a record arrives. Run it only where you control who can see the screen.

`scripts/test_rcpair_receive.py` checks all of this against a running receiver. It also checks
`scripts/testdata/rcpair-send-v1-kat.json`, a fixed set of keys, nonce, token and plaintext with every
intermediate value and the final ciphertext, so the app's encrypt code can be checked against the same
numbers.

## Prerequisites and the walls (read before you start)

- **Developer Mode** must be on. You enable it with your passcode; the device reboots. On managed
  (MDM) devices an administrator can block it.
- **The Developer Disk Image** comes only with Xcode, which runs on a Mac. Connect the device to
  Xcode once and Xcode puts the image on it. This project does not ship or host it. In our testing
  it stayed on after a restart. An iOS update usually means doing it again.
- **Networking.** The device and host reach each other over USB, a LAN, or a VPN such as Tailscale.
  Some developer ports are refused on cellular. mDNS discovery does not cross a VPN, so pair at an
  explicit IP there.
- **Installed-runner path only: signing.** A development-signed build installs only on devices
  registered to your account. A free Apple ID signature expires every 7 days and you re-sign; a paid
  account lasts a year. Apple caps registered devices at 100 per account per year.

## Setup sequence

1. Run `scripts/fetch-deps.sh` to pull idevice (and optionally iCTRL) at pinned versions.
2. Install RC Pair on the device and turn on Developer Mode as it guides you. The device restarts.
3. Pair in RC Pair, then run `scripts/rcpair-receive.py` and scan its QR code. The pairing record
   lands on your computer.
4. Connect the device once to Xcode on a Mac, which puts Apple's Developer Disk Image on it.
5. Run `scripts/connect.sh` to build the idevice tools and walk tunnel -> drive. Follow each tool's
   `-h` for exact flags on your pinned version.
6. Drive the device with the control tools; point your agent at them.

## What is deliberately missing

- **The decision layer / agent** that chooses what to tap and recovers from failures. Bring your
  own, such as Claude Code or Codex.
- **Any Apple binary.** The DDI comes from Xcode on a Mac, once per device.
- **Any hosted service.** We host nothing and never see your device. You run it on your own computer
  or server. Keeping it alive across reboots and network changes is up to you. This repo does not do
  it for you.

## Status

Each step is reproducible but has sharp edges: redoing the Xcode step after an iOS update, cellular port behavior,
mDNS not crossing a VPN, network re-pathing, and for the installed-runner path the 7-day signing
cadence. This is a developer tool, not a turnkey product.
