<!-- RC Pair. Not affiliated with any other company. -->
# RC Pair

Drive a real iPhone or iPad from a plain computer, Linux included, over USB or the network, with
**no Mac left running**. No automation runner is installed on the phone. You turn on Apple's
Developer Mode on **your own device**, with **your own Apple account**, and your computer drives it
directly. An AI
agent, or any program that can speak the control tools, can then read the screen and send taps and
typing.

This is an assembly of existing public work, documented so you can reproduce it from a headless Linux
host. It is **not** a jailbreak and **not** a secret technique. It uses Apple's own developer
services the way they already work. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for exactly how.

## What to know first

- **It needs Developer Mode.** That is an Apple developer feature you turn on with your own
  passcode, on a device you control. The device reboots to enable it.
- **It installs no automation runner on the phone.** The only app is RC Pair. It makes the pairing
  record on the device and sends it once to a computer you choose. The default path then drives the
  device through Apple's own on-device developer services over a tunnel, using
  [jkcoxson/idevice](https://github.com/jkcoxson/idevice) (MIT). An optional path uses an installed,
  development-signed runner ([OpenMinis/iCTRL](https://github.com/OpenMinis/iCTRL), Apache-2.0) for
  structured UI automation; see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Attribution in
  [NOTICE](NOTICE).
- **It needs a Mac with Xcode once per device.** Apple's Developer Disk Image comes only with Xcode,
  and Xcode runs on a Mac. Connect the device to Xcode once and Xcode puts the image on it. After
  that the Mac is not needed. In our testing the image stayed on after a restart. If it is gone,
  connect to Xcode again. An iOS update usually means doing it again.
- **Source only.** We ship no signed binaries and no Apple binaries, and we do not host or supply
  Apple's disk image.

## What this is NOT for

**This is for operating a device you own, with the awareness of the person holding it.** It is not a
covert-monitoring tool and it is built to be useless as one. Using it to watch, track, or control a
phone without the knowledge and agreement of the person using that phone is spyware, it is illegal
in most places, and this project refuses to support it. Read [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md)
before you build, including the plain-language summary of the laws it can break. The consent and
visibility design is in [docs/CONSENT-DESIGN.md](docs/CONSENT-DESIGN.md).

## Quick start

You bring: an iPhone or iPad you own, an Apple account, a Linux host, and a Mac with Xcode for one
setup step per device.

```bash
scripts/fetch-deps.sh        # pulls idevice (and optionally iCTRL) at pinned versions
scripts/connect.sh           # builds the tools and prints the pair -> tunnel -> drive steps
```

Then install RC Pair on the device and turn on Developer Mode as it guides you. After the restart,
pair, run `scripts/rcpair-receive.py` on your computer and scan its QR code with the Camera app.
Connect the device once to Xcode on a Mac for Apple's disk image, then drive it. The full
walkthrough, with every manual step and every place it can break, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hand the pairing to your computer

RC Pair on the phone makes a pairing record for your device. It sends that record to one computer
you choose, once, and to nowhere else. No version of the app sends anything to us or to any server
we run.

```bash
pip install cryptography    # the only required Python package
scripts/rcpair-receive.py   # shows a QR code and a link, waits for the phone, saves the record
```

1. Run `scripts/rcpair-receive.py` on your computer. It makes a new key and a one-time token for
   this run and shows them as a QR code and a link. The QR code appears if `qrencode` or the Python
   `qrcode` package is installed. Without them you get the link only.
2. On the phone, open the Send to your computer step in RC Pair. Scan the QR code with the Camera app,
   or copy the link and tap Paste link.
3. The phone asks you to confirm the computer's name and address. Tap Send.
4. The receiver saves `<udid>.rppair.plist` and `<udid>.json` in `~/.rcpair` (mode 0600), prints the
   device's address, and exits. It takes exactly one upload.

The phone only sends to a private address, meaning your home or office network or a private network
such as Tailscale. The record is encrypted to the key in the QR code, so only that receiver run can
read it. Options are `--host`, `--port` (default 47820), `--name`, `--out`, `--minutes` (default 10),
and `--on-receive CMD`, which runs a command with the two saved file paths. Run it with `-h` for the
rest. The wire format is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and
`scripts/testdata/rcpair-send-v1-kat.json` holds a known-answer vector for it. Check your setup with
`python3 scripts/test_rcpair_receive.py`.

`scripts/rcpair-session.sh start|end` is meant to tell RC Pair a session began or ended. It cannot
work with the idevice tools this repo pins (v0.1.68). Their launch commands send an empty
environment, so the script says that and exits with status 3 instead of pretending.

## Bring your own agent

This repository is the hands. It reaches the device and drives it. The part that *decides* what to
tap is yours to bring. Point your own agent or model at the control tools, such as Claude Code or
Codex.

Everything runs on your own computer or server. We host nothing and never see your phone or what is
on it.

## License

RC Pair is source available under the Functional Source License, Version 1.1, ALv2 Future License
(FSL-1.1-ALv2). See [LICENSE](LICENSE).

You can use it, change it, and run it on your own machines for any purpose except making it
available to others in a competing commercial product or service. Each version becomes Apache-2.0
two years after its release.

Attribution and third-party licenses are in [NOTICE](NOTICE).
