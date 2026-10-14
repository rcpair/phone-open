#!/usr/bin/env bash
# rcpair-session.sh start|end [--name NAME]
#
# Meant to tell RC Pair on the phone that a control session started or ended, by launching the
# app with RC_SESSION=start|end and RC_FROM=NAME in its launch environment.
#
# THIS DOES NOT WORK WITH THE UPSTREAM TOOLS, SO IT STOPS AND SAYS SO.
# The idevice tools at the version scripts/fetch-deps.sh pins (v0.1.68) cannot pass environment
# variables to a launched app.
#   process_control  calls launch_app(bundle_id, None, None, false, false). Environment and arguments
#                    are both None, and the command line takes only a bundle id.
#   app_service      "launch" calls launch_application(bundle_id, &[], true, false, None, None, ...).
#                    Environment is None there too.
# The idevice library does accept an environment (the env_vars and environment parameters of those
# calls). The command-line tools just do not expose it. To use this, write a small launcher against the
# library that sets RC_SESSION and RC_FROM, or run a build of the tools that adds the option. This
# script will not pretend a plain launch carried the environment, because the app would read nothing
# and show the wrong state.
#
# Exit codes. 0 reserved for a launch that really carried the environment (never happens today).
#             2 bad usage. 3 the pinned tools cannot do this.
set -euo pipefail

BUNDLE_ID="${RCPAIR_BUNDLE_ID:-ai.risingcreek.phoneagent}"

usage() {
  cat <<EOF
Usage
  rcpair-session.sh start|end [--name NAME]

What it is for
  Tell RC Pair on the phone that a control session started or ended, by launching $BUNDLE_ID
  with RC_SESSION=start|end and RC_FROM=NAME in its launch environment.

Current status
  It cannot do that with the idevice tools this repo pins (v0.1.68). Their launch commands send an
  empty environment and no arguments, so RC_SESSION and RC_FROM would never reach the app.
  This script checks its arguments, explains that, and exits with status 3. It does not launch
  anything.

  The idevice library can pass an environment. Write a small launcher against it, or use a build of
  the tools that adds the option, and call that instead.

Options
  --name NAME   who is controlling the phone, shown by the app (default the short hostname)
  -h, --help    show this text
EOF
}

action=""
name=""
while [ $# -gt 0 ]; do
  case "$1" in
    start|end)
      if [ -n "$action" ]; then echo "Give start or end once." >&2; usage >&2; exit 2; fi
      action="$1"; shift ;;
    --name)
      if [ $# -lt 2 ] || [ -z "$2" ]; then echo "--name needs a value." >&2; exit 2; fi
      name="$2"; shift 2 ;;
    --name=*)
      name="${1#--name=}"
      if [ -z "$name" ]; then echo "--name needs a value." >&2; exit 2; fi
      shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument '$1'." >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$action" ]; then
  echo "Say start or end." >&2
  usage >&2
  exit 2
fi

if [ -z "$name" ]; then
  name="$(hostname -s 2>/dev/null || echo computer)"
fi

cat >&2 <<EOF
Not sent. The pinned idevice tools cannot pass environment variables to a launched app, so RC Pair
would not learn that the session $action (RC_SESSION=$action, RC_FROM=$name).
Launching $BUNDLE_ID without them would do nothing useful, so nothing was launched.
See the comments at the top of this script for what the tools do and do not support.
EOF
exit 3
