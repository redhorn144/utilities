#!/usr/bin/env bash
#
# Pull a torque data file off a remote host via scp.
#
# Set REMOTE_HOST / REMOTE_DIR / REMOTE_FILE below, then run:
#   ./pull_torques.sh
#
# Any of them can also be overridden per-run from the environment or the
# command line:
#   REMOTE_HOST=user@other.box ./pull_torques.sh
#   ./pull_torques.sh -h user@other.box -d /data/runs -f torques.csv

set -euo pipefail

# ---------------------------------------------------------------- settings --

# Remote host to pull from. Either "hostname" or "user@hostname", or an alias
# from ~/.ssh/config.
#REMOTE_HOST="${REMOTE_HOST:-}"
REMOTE_HOST="aws@aurora.alcf.anl.gov"

# Directory on the remote host that holds the file.
REMOTE_DIR="/lus/flare/projects/AdvanceFusionFission/aws/varyRe"

# File to grab out of REMOTE_DIR.
REMOTE_FILE="nekRS_baf.o8800340"

# Where to drop the file locally.
LOCAL_DIR="./data"

# Name to save it under on arrival. Leave empty to keep REMOTE_FILE's name.
LOCAL_FILE=""

# ------------------------------------------------------------------------- --

usage() {
    cat <<EOF
usage: $(basename "$0") [-h HOST] [-d REMOTE_DIR] [-f REMOTE_FILE] [-o LOCAL_DIR]
       [-n LOCAL_FILE]

  -h HOST        remote host, e.g. user@example.com
  -d REMOTE_DIR  directory on the remote host
  -f REMOTE_FILE file to pull out of REMOTE_DIR
  -o LOCAL_DIR   local destination directory (default: $LOCAL_DIR)
  -n LOCAL_FILE  name to save it under (default: same as REMOTE_FILE)
EOF
}

while getopts ":h:d:f:o:n:" opt; do
    case "$opt" in
        h) REMOTE_HOST="$OPTARG" ;;
        d) REMOTE_DIR="$OPTARG" ;;
        f) REMOTE_FILE="$OPTARG" ;;
        o) LOCAL_DIR="$OPTARG" ;;
        n) LOCAL_FILE="$OPTARG" ;;
        \?) echo "unknown option: -$OPTARG" >&2; usage >&2; exit 2 ;;
        :)  echo "option -$OPTARG needs an argument" >&2; usage >&2; exit 2 ;;
    esac
done

for var in REMOTE_HOST REMOTE_DIR REMOTE_FILE; do
    if [[ -z "${!var}" ]]; then
        echo "error: $var is not set (edit this script or pass it in)" >&2
        usage >&2
        exit 2
    fi
done

# Strip any trailing slash so the joined path stays tidy.
REMOTE_PATH="${REMOTE_DIR%/}/${REMOTE_FILE}"
LOCAL_PATH="${LOCAL_DIR%/}/${LOCAL_FILE:-$(basename "$REMOTE_FILE")}"

mkdir -p "$LOCAL_DIR"

echo "pulling ${REMOTE_HOST}:${REMOTE_PATH} -> ${LOCAL_PATH}"
scp -p "${REMOTE_HOST}:${REMOTE_PATH}" "${LOCAL_PATH}"

echo "wrote ${LOCAL_PATH}"

# Split the log into per-torque series:
#   <local_file>t.1  <- lines tagged 1torqz
#   <local_file>t.2  <- lines tagged 2torqz
for n in 1 2; do
    out="${LOCAL_PATH}t.${n}"
    rc=0
    grep "${n}torqz" "$LOCAL_PATH" > "$out" || rc=$?
    case "$rc" in
        0) echo "wrote ${out} ($(wc -l < "$out") lines)" ;;
        1) echo "warning: no ${n}torqz lines in ${LOCAL_PATH}; ${out} is empty" >&2 ;;
        *) echo "error: grep failed on ${LOCAL_PATH} (exit ${rc})" >&2; exit "$rc" ;;
    esac
done
