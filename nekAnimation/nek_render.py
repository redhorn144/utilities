#!/usr/bin/env pvbatch
"""Render nek5000/nekRS field files to PNG frames through a saved ParaView scene.

Workflow:

    1. Drop the case0.f***** files from a run into ./data (or point --data at
       them) and run this script once with --dry-run to write case.nek5000.
    2. Open case.nek5000 in the ParaView GUI, set up the scene (filters,
       colormaps, camera, Annotate Time, ...) and File > Save State -> scene.pvsm.
    3. Render every field file through that scene:

           pvbatch --force-offscreen-rendering nek_render.py scene.pvsm

    4. Stitch the frames with the ffmpeg command printed at the end.

The reader in the state file is repointed at the .nek5000 file found here, so a
scene saved on a workstation can be replayed on a cluster, or against a
different run of the same case. Frames are named after the nek file number
(frames/case_00042.png <- case0.f00042); existing frames are skipped so an
interrupted render can be resumed.

Large 3D cases: run under MPI. pvbatch executes this script on rank 0 only and
drives the other ranks as servers, so nothing here needs to be rank-aware; the
native Nek5000Reader splits elements across ranks (the VisIt reader does not).
Split a long movie across independent jobs with --part K/N.

Written for ParaView 6.0.0. Everything except the rendering itself is plain
python, so --dry-run works without ParaView installed.

Usage:
    pvbatch nek_render.py scene.pvsm                  # ./data -> ./frames
    pvbatch nek_render.py scene.pvsm --data run3 --out run3/frames
    pvbatch nek_render.py scene.pvsm --resolution 1920 1080 --stride 2
    mpiexec -n 24 pvbatch --force-offscreen-rendering nek_render.py scene.pvsm --part 1/4
    python3 nek_render.py --dry-run                   # just write the .nek5000
"""

import argparse
import functools
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# ------------------------------------------------------------------ settings --

# Reader proxy types that take a .nek5000 file. Either may appear in a state
# saved from the GUI depending on which reader was picked at open time. Only
# the native one distributes the mesh across MPI ranks.
NEK_READER_TYPES = ("Nek5000Reader", "VisItNek5000Reader")
SERIAL_READER_TYPES = ("VisItNek5000Reader",)

# vtkNek5000Reader matches its PointArrayStatus entries to the file's variables
# by position, not by name, and its variables are in header order (U P T S).
# A state saved from the GUI lists the arrays alphabetically, so pushing it
# as-is enables the wrong variables. Entries are re-sorted into this order
# before the state is loaded; anything unknown goes last.
NEK_ARRAY_ORDER = {"Velocity": 0, "Velocity Magnitude": 1, "Pressure": 2, "Temperature": 3}
NEK_SCALAR_ARRAY = re.compile(r"^S(\d+)$")

# nek field file names: <case><fileid>.f<step>, e.g. turbPipe0.f00012.
FIELD_FILE = re.compile(r"^(?P<case>.+?)(?P<fileid>\d)\.f(?P<step>\d{5})$")

FRAME_RATE = 30

# Batch schedulers only show buffered stdout when the job ends; flush so the
# log tracks progress as frames come out.
log = functools.partial(print, flush=True)
warn = functools.partial(print, file=sys.stderr, flush=True)

# --------------------------------------------------------------- field files --


def find_field_files(data_dir):
    """Map case prefix -> sorted list of step numbers found in data_dir.

    Only file id 0 is counted; nek writes one 0-file per step regardless of how
    many output files the step is split across.
    """
    steps = defaultdict(set)
    for p in data_dir.iterdir():
        m = FIELD_FILE.match(p.name)
        if m and m.group("fileid") == "0":
            steps[m.group("case")].add(int(m.group("step")))
    return {case: sorted(s) for case, s in steps.items()}


def pick_case(cases, wanted):
    """Choose the case prefix to render, or die with a helpful message."""
    if not cases:
        sys.exit("error: no <case>0.f***** files found")
    if wanted is not None:
        if wanted not in cases:
            sys.exit(
                f"error: no {wanted}0.f***** files found; "
                f"cases present: {', '.join(sorted(cases))}"
            )
        return wanted
    if len(cases) > 1:
        sys.exit(
            "error: more than one case in the data directory, pick one with "
            f"--case: {', '.join(sorted(cases))}"
        )
    return next(iter(cases))


def contiguous_run(steps):
    """(first, count) of the run of consecutive steps starting at the lowest one.

    The nek5000 reader only knows firsttimestep and numtimesteps, so a gap in
    the numbering ends the usable range.
    """
    first = steps[0]
    count = 1
    while count < len(steps) and steps[count] == first + count:
        count += 1
    if count < len(steps):
        warn(
            f"warning: step numbering has a gap after {first + count - 1}; "
            f"ignoring {len(steps) - count} later file(s)"
        )
    return first, count


def write_nek5000(data_dir, case, first, count):
    """Write <case>.nek5000 next to the field files and return its path.

    The reader uses filetemplate verbatim (not relative to the .nek5000 file),
    so the template has to be absolute.
    """
    path = data_dir / f"{case}.nek5000"
    template = str(data_dir / f"{case}%01d.f%05d")
    path.write_text(
        "nek5000\n"
        "version: 1.0\n"
        f"filetemplate: {template}\n"
        f"firsttimestep: {first}\n"
        f"numtimesteps: {count}\n"
    )
    return path


# ---------------------------------------------------------------- state file --


def find_nek_readers(pvsm):
    """[(id, pipeline name, proxy type)] of the nek readers in a .pvsm file."""
    root = ET.parse(pvsm).getroot()
    names = {}
    for coll in root.iter("ProxyCollection"):
        if coll.get("name") == "sources":
            for item in coll.iter("Item"):
                names[item.get("id")] = item.get("name")

    readers, others = [], set()
    for proxy in root.iter("Proxy"):
        if proxy.get("group") != "sources":
            continue
        ptype = proxy.get("type")
        if ptype in NEK_READER_TYPES:
            pid = proxy.get("id")
            readers.append((pid, names.get(pid, ""), ptype))
        elif ptype.endswith("Reader"):
            others.add(ptype)

    if not readers:
        hint = f" (readers present: {', '.join(sorted(others))})" if others else ""
        sys.exit(f"error: {pvsm} has no Nek5000 reader{hint}")
    return readers


def nek_array_rank(name):
    if name in NEK_ARRAY_ORDER:
        return NEK_ARRAY_ORDER[name]
    m = NEK_SCALAR_ARRAY.match(name)
    return 4 + int(m.group(1)) if m else 1000


def patch_state(pvsm, patched):
    """Write a copy of the state whose native readers survive being loaded.

    Two vtkNek5000Reader quirks are worked around: the array selection is put
    in the reader's own order (see NEK_ARRAY_ORDER), and 'Velocity' is enabled
    whenever the derived 'Velocity Magnitude' is, because the reader only
    computes the magnitude while reading the velocity (on its own it fills the
    array with misaligned velocity components). Returns the path to load: the
    original when nothing needed changing.
    """
    tree = ET.parse(pvsm)
    changed = False
    for proxy in tree.getroot().iter("Proxy"):
        if proxy.get("group") != "sources" or proxy.get("type") != "Nek5000Reader":
            continue
        prop = next((p for p in proxy.iter("Property")
                     if p.get("name") == "PointArrayStatus"), None)
        if prop is None:
            continue
        elems = [e for e in prop if e.tag == "Element"]
        pairs = [(elems[i].get("value"), elems[i + 1].get("value"))
                 for i in range(0, len(elems) - 1, 2)]
        status = dict(pairs)
        if status.get("Velocity Magnitude") == "1" and status.get("Velocity") == "0":
            warn("warning: 'Velocity Magnitude' needs 'Velocity'; enabling it in the reader")
            status["Velocity"] = "1"
        wanted = [(n, status[n]) for n in sorted(status, key=nek_array_rank)]
        if wanted == pairs:
            continue
        at = list(prop).index(elems[0])
        for e in elems:
            prop.remove(e)
        for i, (name, value) in enumerate(wanted):
            for j, v in ((2 * i, name), (2 * i + 1, value)):
                e = ET.Element("Element", index=str(j), value=v)
                e.tail = elems[0].tail  # keep the indentation readable
                prop.insert(at + j, e)
        changed = True
    if not changed:
        return pvsm
    tree.write(patched)
    log(f"reader array selection reordered for loading -> {patched}")
    return patched


# ----------------------------------------------------------------- rendering --


def frame_path(out_dir, case, step):
    return out_dir / f"{case}_{step:05d}.png"


def take_part(items, part):
    """The K-th of N contiguous chunks of items, from a 'K/N' string."""
    try:
        k, n = (int(x) for x in part.split("/"))
    except ValueError:
        sys.exit(f"error: --part expects K/N, got {part!r}")
    if not 1 <= k <= n:
        sys.exit(f"error: --part {part}: need 1 <= K <= N")
    per = -(-len(items) // n)  # ceil
    return items[(k - 1) * per : k * per]


def check_arrays(pv, readers, t):
    """Die if a reader does not produce every array its selection enables.

    A representation colored by a missing array silently falls back to solid
    white, which is much harder to notice in a batch of frames than an error.
    """
    for _, name, _ in readers:
        reader = pv.FindSource(name)
        if reader is None or not hasattr(reader, "PointArrayStatus"):
            continue
        reader.UpdatePipeline(t)
        present = list(reader.PointData.keys())
        log(f"reader '{name}' point arrays: {', '.join(present) or 'none'}")
        missing = [n for n in reader.PointArrayStatus if n not in present]
        if missing:
            sys.exit(
                f"error: reader '{name}' did not produce {', '.join(missing)}; "
                "check the reader's array selection in the scene"
            )


def render(pvsm, nek5000, readers, frames, resolution, use_layout):
    """Load the scene, then save one screenshot per (step, path, time index)."""
    from paraview import simple as pv
    from paraview.servermanager import vtkProcessModule, vtkPVSession

    nranks = vtkProcessModule.GetProcessModule().GetNumberOfLocalPartitions()
    log(f"rendering on {nranks} rank(s)")
    serial = [name for _, name, ptype in readers if ptype in SERIAL_READER_TYPES]
    if nranks > 1 and serial:
        warn(
            f"warning: reader(s) {', '.join(serial)} load the whole mesh on one "
            "rank; rebuild the scene with the native 'Nek5000 Files' reader to "
            "spread it across MPI ranks"
        )

    # The default CLIENT location deadlocks under mpiexec (rank 0 reads the
    # file while the satellites wait on a collective); SERVERS works in both
    # serial and MPI pvbatch since rank 0 is a server too.
    pv.LoadState(
        str(pvsm),
        filenames=[
            {"name": name, "id": pid, "FileName": str(nek5000)}
            for pid, name, _ in readers
        ],
        location=vtkPVSession.SERVERS,
    )

    scene = pv.GetAnimationScene()
    scene.UpdateAnimationUsingDataTimeSteps()
    times = list(pv.GetTimeKeeper().TimestepValues)
    if times:
        check_arrays(pv, readers, times[0])

    view = pv.GetActiveView()
    if view is None:
        sys.exit("error: state file has no render view")
    target = pv.GetLayout(view) if use_layout else view
    if resolution is None:
        resolution = list(view.ViewSize)

    todo = [(step, path, i) for step, path, i in frames if i < len(times)]
    if len(todo) < len(frames):
        warn(
            f"warning: reader reports {len(times)} timesteps, "
            f"expected {max(i for _, _, i in frames) + 1}; rendering what it has"
        )

    t0 = time.time()
    for n, (step, path, i) in enumerate(todo, 1):
        t_frame = time.time()
        scene.AnimationTime = times[i]
        pv.SaveScreenshot(str(path), target, ImageResolution=resolution)
        now = time.time()
        eta = (now - t0) / n * (len(todo) - n)
        log(
            f"[{n}/{len(todo)}] step {step}  t={times[i]:.6g}  -> {path.name}  "
            f"({now - t_frame:.1f}s, ~{eta / 60:.1f} min left)"
        )


def ffmpeg_hint(out_dir, case):
    # The pad filter keeps libx264 happy when a view size is odd.
    return (
        f"ffmpeg -framerate {FRAME_RATE} -pattern_type glob -i '{out_dir}/{case}_*.png' "
        f"-c:v libx264 -pix_fmt yuv420p -vf \"pad=ceil(iw/2)*2:ceil(ih/2)*2\" {case}.mp4"
    )


# ---------------------------------------------------------------------- main --


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("scene", type=Path, nargs="?",
                    help=".pvsm state file saved from the GUI (optional with --dry-run)")
    ap.add_argument("--data", type=Path, default=Path("data"),
                    help="directory holding <case>0.f***** files (default: data)")
    ap.add_argument("--case", help="case prefix; auto-detected if only one is present")
    ap.add_argument("--nek5000", type=Path,
                    help="use this .nek5000 file instead of generating one")
    ap.add_argument("--out", type=Path, default=Path("frames"),
                    help="frame directory (default: frames)")
    ap.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"),
                    help="frame size in pixels (default: view size saved in the state)")
    ap.add_argument("--start", type=int, default=0, help="first timestep index")
    ap.add_argument("--end", type=int, help="stop before this timestep index")
    ap.add_argument("--stride", type=int, default=1, help="render every Nth timestep")
    ap.add_argument("--part", metavar="K/N",
                    help="render only the K-th of N contiguous chunks of the frames, "
                         "so N independent jobs can share one movie")
    ap.add_argument("--layout", action="store_true",
                    help="capture the whole layout instead of the active view")
    ap.add_argument("--force", action="store_true",
                    help="re-render frames that already exist")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the .nek5000 and list the frames, but do not render")
    return ap.parse_args(argv)


def main(argv=None):
    # pvbatch/pvpython hand the script its own argv, so sys.argv[1:] is ours.
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.scene is None and not args.dry_run:
        sys.exit("error: a .pvsm scene file is required unless --dry-run is given")
    if args.scene is not None and not args.scene.is_file():
        sys.exit(f"error: {args.scene} not found")
    data_dir = args.data.resolve()
    if not data_dir.is_dir():
        sys.exit(f"error: {data_dir} is not a directory")

    cases = find_field_files(data_dir)
    case = pick_case(cases, args.case)
    first, count = contiguous_run(cases[case])
    log(f"case {case}: steps {first}..{first + count - 1} ({count} files) in {data_dir}")

    if args.nek5000 is not None:
        nek5000 = args.nek5000.resolve()
        if not nek5000.is_file():
            sys.exit(f"error: {nek5000} not found")
    else:
        nek5000 = write_nek5000(data_dir, case, first, count)
        log(f"wrote {nek5000}")

    readers = find_nek_readers(args.scene) if args.scene is not None else []
    for pid, name, ptype in readers:
        log(f"reader '{name}' ({ptype}, id {pid}) -> {nek5000}")

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = args.scene
    if scene is not None:
        scene = patch_state(scene, out_dir / f"{scene.stem}.loaded.pvsm")

    indices = range(count)[args.start:args.end:args.stride]
    frames = [(first + i, frame_path(out_dir, case, first + i), i) for i in indices]
    # Chunk before dropping existing frames so every job of a K/N set carves
    # up the same list no matter when it starts.
    if args.part is not None:
        frames = take_part(frames, args.part)
        log(f"part {args.part}: {len(frames)} frame(s)")
    if not args.force:
        skipped = sum(path.exists() for _, path, _ in frames)
        frames = [f for f in frames if not f[1].exists()]
        if skipped:
            log(f"skipping {skipped} existing frame(s); use --force to redo them")
    log(f"{len(frames)} frame(s) to render into {out_dir}")

    if args.dry_run:
        for step, path, _ in frames:
            log(f"  step {step} -> {path}")
    elif frames:
        render(scene, nek5000, readers, frames, args.resolution, args.layout)

    log("\nstitch with:\n  " + ffmpeg_hint(args.out, case))


if __name__ == "__main__":
    main()
