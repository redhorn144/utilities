#!/usr/bin/env bash
#
# Split every log file in the data folder into per-torque series.
#
# This is the batch version of the grep step at the tail of pull_torques.sh:
# for each matched log it writes
#   <log>t.1  <- lines tagged 1torqz
#   <log>t.2  <- lines tagged 2torqz
# so the output names line up with what the plotter expects.
#
#   ./split_torques.sh
#   ./split_torques.sh -o /some/other/dir -p 'run*'

set -euo pipefail

# ---------------------------------------------------------------- settings --

# Directory holding the logs to split.
LOCAL_DIR="./data"

# Glob for the log files inside LOCAL_DIR.
LOG_GLOB="log*"

# ------------------------------------------------------------------------- --

usage() {
    cat <<EOF
usage: $(basename "$0") [-o LOCAL_DIR] [-p LOG_GLOB]

  -o LOCAL_DIR  directory holding the logs (default: $LOCAL_DIR)
  -p LOG_GLOB   glob for log files inside LOCAL_DIR (default: $LOG_GLOB)
EOF
}

while getopts ":o:p:" opt; do
    case "$opt" in
        o) LOCAL_DIR="$OPTARG" ;;
        p) LOG_GLOB="$OPTARG" ;;
        \?) echo "unknown option: -$OPTARG" >&2; usage >&2; exit 2 ;;
        :)  echo "option -$OPTARG needs an argument" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! -d "$LOCAL_DIR" ]]; then
    echo "error: no such directory: $LOCAL_DIR" >&2
    exit 2
fi

shopt -s nullglob

found=0
for log in "${LOCAL_DIR%/}"/$LOG_GLOB; do
    # Skip directories and the t.1 / t.2 files a previous run left behind.
    [[ -f "$log" ]] || continue
    [[ "$log" == *t.[12] ]] && continue

    found=$((found + 1))
    echo "splitting ${log}"

    for n in 1 2; do
        out="${log}t.${n}"
        rc=0
        grep "${n}torqz" "$log" > "$out" || rc=$?
        case "$rc" in
            0) echo "  wrote ${out} ($(wc -l < "$out") lines)" ;;
            1) echo "warning: no ${n}torqz lines in ${log}; ${out} is empty" >&2 ;;
            *) echo "error: grep failed on ${log} (exit ${rc})" >&2; exit "$rc" ;;
        esac
    done
done

if (( found == 0 )); then
    echo "error: no files matching ${LOG_GLOB} in ${LOCAL_DIR}" >&2
    exit 1
fi

echo "split ${found} log file(s)"
