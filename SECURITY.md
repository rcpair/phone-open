# Security and misuse reporting

## Reporting a vulnerability

Email support@rcpair.si with a description and steps to reproduce. Please do not open a public issue
for a security vulnerability. We aim to acknowledge within a few business days.

## Reporting misuse

This software is for operating a device with the awareness of the person using it (see
[ACCEPTABLE_USE.md](ACCEPTABLE_USE.md)). If you believe it is being used to monitor or control
someone's phone without their knowledge, report it to support@rcpair.si.

If you are a person who may be affected by unwanted monitoring, these resources can help:
- Coalition Against Stalkerware, https://stopstalkerware.org
- In the US, the National Domestic Violence Hotline at 1-800-799-7233.

## Scope notes

- No Apple binaries (including the Developer Disk Image) are distributed with this project. You put
  Apple's disk image in a folder on your own computer, from Xcode on your Mac or from a public GitHub
  copy you pull yourself. The repository holds only SHA-256 hashes of it, in
  `scripts/ddi-sha256.txt`.
- The fill scripts treat those files as untrusted. The GitHub script fetches one pinned commit and
  checks every file's SHA-256 before it parses any of them. Both scripts refuse symlinks, absolute
  or `..` paths in the manifest, any path that resolves outside the source folder, and oversized
  files, and they swap the image folder in one step so a failed fill leaves the old one in place.
  The device also checks the image: Apple's signing server personalizes it for that device, and the
  device refuses one it did not sign. A way past any of these checks is in scope.
- No secrets, tokens, or private keys belong in this repository. If you find one, report it as a
  vulnerability.
