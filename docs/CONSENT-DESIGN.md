# Consent and anti-misuse design

The goal is to make this safe to use for operating a device with its user's awareness, and useless
for covert monitoring. It does not try to prove ownership. That is impossible. It **removes
secrecy**, which is what stalkerware requires.

## What we can and cannot do

Cannot, by anyone, including Apple:
- Prove a person legally owns a device.
- Read a person's intent.
- Stop a determined developer from forking published code and stripping safeguards.

Can:
- Make the tool **impossible to hide** from the person using the device.
- Require the person **holding the device** to set it up and let them **end it at any time**.
- Specify that the on-device layer must refuse to run a session unless a consent record exists and
  the visible indicator is active. That layer is not in this repo.
- Keep a record that consent was given, as a deterrent and a legal artifact.

A tool the subject can always see and always switch off cannot be used to spy, because spying needs
to be hidden.

## The requirements (mandatory)

1. **Visible presence.** The app shows a normal home-screen icon and does not hide itself. No
   disguised names, no invisible installs. Hiding the app is the single clearest stalkerware trait;
   this project does the opposite.
2. **Holder-performed setup.** The person with the device installs it, enables Developer Mode with
   their own passcode, and taps to pair. That physical sequence is the act of consent and cannot be
   done remotely.
3. **In-session indicator.** While a remote session is active, the device shows a persistent
   indicator so the person holding it always knows the device is being controlled, and a short
   sound plays when a session starts. The indicator is always on for the whole session and the
   holder cannot remove or hide it. There is no device-side switch to turn it off, because an
   indicator the holder can dismiss is the stalkerware failure mode. It is responsive: it adapts to
   device size, from an iPhone mini to an iPad Pro, and to orientation, repositioning when the
   device rotates so it is never sideways or off-screen and always sits in a consistent, readable
   spot. Its job is to tell the person physically holding the device that a session is live.
4. **One-tap revocation on the device.** The device shows what it is paired to and can unpair and
   stop any live session from the device itself, without help from the computer driving it.
5. **No stealth capture.** No background screen recording, keylogging, or location collection. The
   tool acts only during a visible session and captures only what that session needs.
6. **Session transparency.** The device keeps a visible log of sessions (when, how long) that the
   holder can read.
7. **Ownership and authorization attestation.** At pairing, the operator affirms they own the device
   or are authorized by its user, and the user confirms on the device. This is a consent record and
   a legal artifact. It is a deterrent, not proof.

## Where each requirement lives

Requirements 1-7 are documented here as mandatory. Published source can be forked and the
safeguards removed, and we cannot prevent that. This project runs no service and enforces nothing. Anyone who
builds on these hands should treat requirements 1-7 as conditions of use.

## Limits

- A fork that strips the indicator and consent screen can exist. It would violate
  [ACCEPTABLE_USE.md](../ACCEPTABLE_USE.md) and is the author's own legal exposure, not a supported
  path.
- On iOS, the operating system's own "screen is being shared" indicator may not light for this
  control method, which is why the design requires the app's **own** persistent indicator rather
  than relying on the OS. Verify the indicator appears on your device and OS version before
  trusting it.
- Consent given once must be revocable forever. A record that cannot be withdrawn is a stalkerware
  trait; revocation (requirement 4) is not optional.
