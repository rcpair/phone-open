# Acceptable use

This project exists to let you operate **a device you own**, with the **awareness and agreement of
the person using it**. That is the only use it is built for and the only use it will be supported
for.

## Allowed

- Operating your own phone or tablet, from your own server, with your own Apple account.
- Operating a device whose user knows it is enrolled, has agreed, and can see and stop it at any
  time: your own test devices, a device you administer for someone who asked you to, an accessibility
  setup the user chose.
- Building agents and automations on top of the API for any of the above.

## Not allowed

- Installing or running this on a phone to monitor, track, read, or control it **without the
  knowledge and ongoing agreement of the person who uses that phone.** Watching a spouse, partner,
  child-as-surveillance, employee, or anyone else covertly is stalkerware. It is illegal in most
  jurisdictions and it is the exact thing this project is designed to be useless for.
- Removing, hiding, or disabling the visibility and consent features described in
  [docs/CONSENT-DESIGN.md](docs/CONSENT-DESIGN.md), or distributing a build that does so.
- Any use that violates Apple's developer terms, your local law, or another person's rights.

## How the design backs this up

We cannot prove who owns a device or read anyone's intent, and we do not pretend to. Instead the
design removes **secrecy**, which is what covert monitoring depends on. It requires the app to be
visible and not hideable, the person holding the device to set it up and be able to revoke it, and
no session to run invisibly. A tool the subject can always see and switch off is not a spying tool.
Details and the limits are in [docs/CONSENT-DESIGN.md](docs/CONSENT-DESIGN.md).

Because the source is available, someone can fork it and tear the safeguards out. That is true of
every tool like this and we cannot stop it in code we publish. What we can do, and do, is document
the safeguards as mandatory and refuse to help anyone weaken them.

## The law (plain English, and not legal advice)

Using this software to access, watch, track, record, or control someone's phone **without their
knowledge and permission** can be a serious crime. Depending on where you are and what you do, you
could be breaking laws such as:

- **Computer Fraud and Abuse Act** (18 U.S.C. § 1030), which covers accessing a device or account
  you are not authorized to access.
- **The federal Wiretap Act / ECPA** (18 U.S.C. § 2511), which covers intercepting someone's calls,
  messages, or other communications.
- **Stored Communications Act** (18 U.S.C. § 2701), which covers getting into stored messages or
  data without authorization.
- **Section 5 of the FTC Act** (15 U.S.C. § 45), which the Federal Trade Commission has used against
  the makers and sellers of covert phone-monitoring ("stalkerware") tools, in cases that named the
  individuals involved as well as their companies.
- **State computer-crime, electronic-surveillance, and anti-stalking laws**, including
  "all-party consent" (two-party consent) wiretap laws that require **everyone** on a call or in a
  conversation to agree before it is recorded or intercepted.

Outside the United States, similar or stricter laws apply (for example the GDPR and national
computer-misuse and surveillance laws).

These are examples, not a complete list, and **this is not legal advice**. Laws differ by country
and state and change over time. If you are not certain your use is lawful, talk to a lawyer before
you use this software.

**Do not use this software to monitor anyone without their clear, ongoing, informed consent.** If
you do, that is on you, not on this project.

## Reporting misuse

If you believe this software is being used to monitor someone without their knowledge, see
[SECURITY.md](SECURITY.md) for how to report it. Resources for people affected by digital
monitoring: the Coalition Against Stalkerware (stopstalkerware.org).
