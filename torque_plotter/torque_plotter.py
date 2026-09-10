#!/usr/bin/env python3
"""Plot torque histories from the per-body log files written by pull_torques.sh.

Each log is split into <prefix><N>t.1 and <prefix><N>t.2 (bodies 1 and 2). The
columns are, zero-indexed:

    0  step   1  time   2  total torque   3  pressure torque   4  viscous torque

Body 1's torque is negated by default so the two bodies plot with the same sign.

Usage:
    ./torque_plotter.py             # every log found, stitched together in time
    ./torque_plotter.py 6           # just log6
    ./torque_plotter.py 2 3 4       # logs 2, 3 and 4, stitched
"""

import argparse
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --------------------------------------------------------------------- style --

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#dcdbd6"
SERIES = {1: "#2a78d6", 2: "#eb6834"}

BODIES = (1, 2)

# Column plotted against time, zero-indexed: 2 = total, 3 = pressure, 4 = viscous.
TORQUE_COL = 2

# ----------------------------------------------------------------- data load --


def log_path(data_dir, prefix, number, body):
    """Path of the split file for one log number and one body."""
    return data_dir / f"{prefix}{number}t.{body}"


def find_logs(data_dir, prefix):
    """Log numbers present in data_dir, ascending. Body 1 defines the set."""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)t\.1$")
    numbers = sorted(
        int(m.group(1))
        for m in (pattern.match(p.name) for p in data_dir.iterdir())
        if m
    )
    return numbers


def read_series(path, time_col, torque_col, negate):
    """Read (time, torque) pairs out of one split log file.

    Lines that do not carry both columns as floats are skipped -- a run that was
    killed mid-write leaves a torn last line.
    """
    times, torques = [], []
    needed = max(time_col, torque_col) + 1
    with path.open() as fh:
        for line in fh:
            fields = line.split()
            if len(fields) < needed:
                continue
            try:
                t = float(fields[time_col])
                q = float(fields[torque_col])
            except ValueError:
                continue
            times.append(t)
            torques.append(-q if negate else q)
    return times, torques


def stitch(chunks):
    """Concatenate per-log (time, torque) chunks, dropping restart overlap.

    A restarted run replays some time that the previous log already covered, so
    anything at or past the next log's first sample is dropped from what came
    before. Returns the joined series plus the time of each seam.
    """
    times, torques, seams = [], [], []
    for chunk_times, chunk_torques in chunks:
        if not chunk_times:
            continue
        if times:
            start = chunk_times[0]
            keep = len(times)
            while keep and times[keep - 1] >= start:
                keep -= 1
            del times[keep:]
            del torques[keep:]
            seams.append(start)
        times.extend(chunk_times)
        torques.extend(chunk_torques)
    return times, torques, seams


# ------------------------------------------------------------------- plotting --


def plot(series, seams, title, out_path, dpi, show):
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=dpi)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for seam in seams:
        ax.axvline(seam, color=GRID, linewidth=1, zorder=0)

    for body, (times, torques, label) in sorted(series.items()):
        ax.plot(times, torques, color=SERIES[body], linewidth=2, label=label)

    ax.set_xlabel("time", color=INK_MUTED)
    ax.set_ylabel("torque (z)", color=INK_MUTED)
    ax.set_title(title, color=INK, loc="left", fontsize=12)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_MUTED, length=4, width=1.2)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(INK_MUTED)
        ax.spines[spine].set_linewidth(1.2)
    legend = ax.legend(frameon=False, labelcolor=INK_MUTED)
    legend.set_zorder(5)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    print(f"wrote {out_path}")
    if show:
        matplotlib.use("TkAgg", force=True)
        plt.show()
    plt.close(fig)


# ----------------------------------------------------------------------- cli --


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "logs",
        nargs="*",
        type=int,
        help="log number(s) to plot; omit for every log in the data directory",
    )
    parser.add_argument("-d", "--data-dir", default="data", type=Path)
    parser.add_argument("-p", "--prefix", default="log", help="log file prefix")
    parser.add_argument("-o", "--output", default=None, type=Path)
    parser.add_argument("--time-col", default=1, type=int, help="0-indexed")
    parser.add_argument(
        "--torque-col", default=TORQUE_COL, type=int, help="0-indexed"
    )
    parser.add_argument(
        "--no-negate",
        action="store_true",
        help="plot body 1's torque as written instead of negating it",
    )
    parser.add_argument("--dpi", default=150, type=int)
    parser.add_argument("--show", action="store_true", help="open a window too")
    args = parser.parse_args(argv)

    if not args.data_dir.is_dir():
        parser.error(f"no such data directory: {args.data_dir}")

    numbers = args.logs or find_logs(args.data_dir, args.prefix)
    if not numbers:
        parser.error(f"no {args.prefix}<N>t.1 files in {args.data_dir}")

    series = {}
    seams = []
    for body in BODIES:
        chunks = []
        for number in numbers:
            path = log_path(args.data_dir, args.prefix, number, body)
            if not path.exists():
                print(f"warning: no {path}, skipping", file=sys.stderr)
                continue
            chunks.append(
                read_series(
                    path,
                    args.time_col,
                    args.torque_col,
                    negate=(body == 1 and not args.no_negate),
                )
            )
        times, torques, body_seams = stitch(chunks)
        if not times:
            continue
        label = f"body {body}"
        if body == 1 and not args.no_negate:
            label += " (negated)"
        series[body] = (times, torques, label)
        seams = seams or body_seams

    if not series:
        parser.error("no usable torque data found")

    if len(numbers) == 1:
        title = f"{args.prefix}{numbers[0]} torque"
    else:
        title = f"{args.prefix}{numbers[0]}-{args.prefix}{numbers[-1]} torque"

    out_path = args.output
    if out_path is None:
        stem = "-".join(str(n) for n in numbers)
        out_path = Path(f"{args.prefix}{stem}_torque.png")

    plot(series, seams, title, out_path, args.dpi, args.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
